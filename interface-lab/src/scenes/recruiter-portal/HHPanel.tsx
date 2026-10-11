import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowUpRight, Link2, RefreshCw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { recruiterRequest, type HHCollection, type HHState, type RecruiterCandidate, type Vacancy } from './api';
import './hh.css';

export type HHCallback = { code: string; state: string; denied: boolean };
let callbackCache: HHCallback | null | undefined;

/** Called before the recruiter portal's auth/network effects. Never persisted. */
export function captureHHCallback(): HHCallback | null {
  if (callbackCache !== undefined) return callbackCache;
  const url = new URL(location.href);
  if (url.searchParams.get('hhCallback') !== '1') return (callbackCache = null);
  callbackCache = { code: url.searchParams.get('code') || '', state: url.searchParams.get('state') || '', denied: url.searchParams.has('error') };
  for (const key of ['code', 'state', 'error', 'error_description', 'hhCallback']) url.searchParams.delete(key);
  history.replaceState(history.state, '', url.pathname + url.search + url.hash);
  return callbackCache;
}

export function clearHHCallback() { callbackCache = null; }

const eventLabels: Record<string, string> = { interview_booked: 'Назначено интервью → ссылка на встречу', interview_completed: 'Завершено интервью → ссылка на профиль' };
const deliveryLabels: Record<string, string> = { queued: 'В очереди', sending: 'Отправляется', sent: 'Принято HH', retry: 'Повторим позже', unknown: 'Результат не подтверждён', needs_review: 'Нужна проверка в HH', blocked: 'HH не разрешил отправку', failed: 'HH отклонил сообщение', cancelled: 'Отправка отменена' };
const skipLabels: Record<string, string> = { existing_invitation: 'Ссылка уже выдана вручную — отправьте её из HH', account_active: 'Кабинет уже активирован', ambiguous_audience: 'Несколько подходящих откликов — отправка пропущена', invitation_unavailable: 'Ссылка недоступна для этой встречи' };

export function HHPanel({ token, vacancies, candidates, oauth, onOAuthDone, onImported, onCandidate }: {
  token: string; vacancies: Vacancy[]; candidates: RecruiterCandidate[]; oauth: HHCallback | null;
  onOAuthDone: () => void; onImported: () => void; onCandidate: (id: string) => void;
}) {
  const [state, setState] = useState<HHState | null>(null);
  const [connectionId, setConnectionId] = useState('');
  const [vacancyId, setVacancyId] = useState('');
  const [localId, setLocalId] = useState('');
  const [collections, setCollections] = useState<HHCollection[] | null>(null);
  const [collectionIds, setCollectionIds] = useState<string[]>([]);
  const [event, setEvent] = useState('');
  const [template, setTemplate] = useState('');
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const sequence = useRef(0);
  const callbackHandled = useRef(false);
  const mutation = useRef(false);
  const chosen = state?.vacancies.find(v => v.id === vacancyId);
  const connection = state?.connections.find(c => c.id === connectionId);

  const refresh = useCallback(async () => {
    if (!token || mutation.current) return;
    const seq = ++sequence.current;
    try {
      const query = new URLSearchParams({ connectionId, vacancyId });
      const next = await recruiterRequest<HHState>('hh/state?' + query, token);
      if (seq !== sequence.current) return;
      setState(next);
      // Choosing the connected account is harmless. A vacancy/audience is always explicit.
      if (!connectionId && next.connections.length === 1) setConnectionId(next.connections[0].id);
    } catch (cause) { if (seq === sequence.current) setError((cause as Error).message); }
  }, [token, connectionId, vacancyId]);

  useEffect(() => {
    setState(null); setCollections(null); setCollectionIds([]); setEnabled(false); setEvent(''); setTemplate('');
    void refresh();
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 12000);
    return () => { sequence.current++; clearInterval(timer); };
  }, [refresh]);
  useEffect(() => { setLocalId(chosen?.localId || ''); }, [chosen?.localId, vacancyId]);

  async function act<T,>(action: () => Promise<T>, message = ''): Promise<T | undefined> {
    if (mutation.current) return;
    mutation.current = true; sequence.current++; setBusy(true); setError(''); setNotice('');
    try { const result = await action(); if (message) setNotice(message); return result; }
    catch (cause) { setError((cause as Error).message); return; }
    finally { mutation.current = false; setBusy(false); void refresh(); }
  }

  useEffect(() => {
    if (!token || !oauth || callbackHandled.current) return;
    callbackHandled.current = true;
    if (oauth.denied || !oauth.code || !oauth.state) { setError('Подключение HH отменено. Можно попробовать ещё раз.'); onOAuthDone(); return; }
    void act(() => recruiterRequest<{ connection: { id: string } }>('hh/oauth/complete', token, { code: oauth.code, state: oauth.state }), 'HH подключён. Теперь выберите вакансию.')
      .then(result => { if (result) setConnectionId(result.connection.id); onOAuthDone(); });
    // One exchange only. Callback values are never stored or logged.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, oauth]);

  async function connect() {
    const result = await act(() => recruiterRequest<{ url: string }>('hh/oauth/start', token, {}));
    if (result) {
      const url = new URL(result.url);
      if (url.origin === 'https://hh.ru' && url.pathname === '/oauth/authorize') location.assign(result.url);
      else setError('Не удалось начать подключение HH.');
    }
  }
  async function loadCollections() {
    const result = await act(() => recruiterRequest<{ collections: HHCollection[] }>('hh/collections?' + new URLSearchParams({ connectionId, vacancyId }), token));
    if (result) { setCollections(result.collections); setCollectionIds([]); }
  }
  async function importResponses() {
    const result = await act(() => recruiterRequest<{ negotiations: number; resumes: number; skipped?: number }>('hh/responses/import', token, { connectionId, vacancyId, collectionIds }));
    if (result) { setNotice(`Импортировано профилей: ${result.resumes}. Откликов: ${result.negotiations}.${result.skipped ? ` HH не предоставил резюме в ${result.skipped} откликах.` : ''}`); onImported(); }
  }
  async function saveRule(e: FormEvent) {
    e.preventDefault();
    const result = await act(() => recruiterRequest('hh/rules', token, { connectionId, vacancyId, event, template, enabled }), enabled ? 'Правило включено для новых событий.' : 'Правило сохранено выключенным.');
    if (result) setEnabled(false);
  }
  function name(id: string) { return candidates.find(p => p.id === id)?.name || 'Кандидат из HH'; }
  const imported = [...new Map((state?.negotiations || []).map(n => [n.candidate_id, n])).values()];

  return <div className="rp-hh">
    {error && <p role="alert" className="rp-error">{error}</p>}
    {notice && <p role="status" className="rp-notice">{notice}</p>}
    {!state ? <div className="rp-card"><p>{error ? 'Данные HH не загрузились.' : 'Проверяем подключение…'}</p>{error && <Button variant="secondary" onClick={() => void refresh()}>Повторить</Button>}</div> : <>
      <section className="rp-card rp-hh-connection">
        <div><h2>{!state.configured || !connection ? 'HH не подключён' : connection.status === 'connected' ? 'HH подключён' : 'Нужно обновить доступ HH'}</h2><p className="rp-muted">Вакансии и отклики вашего аккаунта. Профили появятся в разделе «Кандидаты».</p>{!state.configured && <p className="rp-muted">Приложение HH ещё не настроено. После настройки сервера здесь можно будет подключить аккаунт.</p>}</div>
        <Button variant={connection?.status === 'connected' ? 'secondary' : 'default'} disabled={busy || !state.configured} onClick={() => void connect()}><Link2 size={16}/>{connection ? 'Подключить заново' : 'Подключить HH'}</Button>
      </section>
      {state.connections.length > 1 && <label className="rp-hh-label">Аккаунт HH<select value={connectionId} onChange={e => { setConnectionId(e.target.value); setVacancyId(''); }}><option value="">Выберите аккаунт</option>{state.connections.map(c => <option key={c.id} value={c.id}>Работодатель {c.employer_id} · менеджер {c.manager_id}</option>)}</select></label>}
      {connectionId && <section className="rp-card">
        <div className="rp-section-heading"><h3>Вакансия HH</h3><Button variant="ghost" disabled={busy || !state.configured} onClick={() => void act(() => recruiterRequest('hh/vacancies/sync', token, { connectionId }), 'Список вакансий обновлён.')}><RefreshCw size={16}/>Обновить вакансии</Button></div>
        <label className="rp-hh-label">Выберите вакансию<select value={vacancyId} disabled={busy} onChange={e => setVacancyId(e.target.value)}><option value="">Вакансия не выбрана</option>{state.vacancies.filter(v => v.active).map(v => <option key={v.id} value={v.id}>{v.title}{v.city ? ` · ${v.city}` : ''}</option>)}</select></label>
        {!state.vacancies.some(v => v.active) && <p className="rp-muted">Обновите список, чтобы получить доступные вакансии из HH.</p>}
        {chosen && <form className="rp-hh-binding" onSubmit={e => { e.preventDefault(); void act(() => recruiterRequest('hh/vacancies/link', token, { connectionId, vacancyId, localId }), 'Вакансии связаны.'); }}>
          <label className="rp-hh-label">Вакансия в HR Vision<select value={localId} required disabled={busy} onChange={e => setLocalId(e.target.value)}><option value="">Выберите вакансию</option>{vacancies.map(v => <option key={v.id} value={v.id}>{v.title} · {v.company}</option>)}</select></label><Button variant="secondary" disabled={busy || !localId || localId === chosen.localId}>Связать</Button>
          {!vacancies.length && <p className="rp-muted">Сначала добавьте вакансию в разделе «Вакансии».</p>}
        </form>}
      </section>}
      {chosen?.localId && <>
        <section className="rp-card">
          <div className="rp-section-heading"><h3>Отклики</h3><Button variant="secondary" disabled={busy} onClick={() => void loadCollections()}>Выбрать группы откликов</Button></div>
          {collections && <><fieldset className="rp-hh-collections"><legend>Импортировать из HH</legend>{collections.length ? collections.map(c => <label className="rp-check" key={c.id}><input type="checkbox" checked={collectionIds.includes(c.id)} onChange={e => setCollectionIds(previous => e.target.checked ? [...previous, c.id] : previous.filter(id => id !== c.id))}/><span>{c.name || c.id}{c.count !== null ? ` · ${c.count}` : ''}</span></label>) : <p className="rp-muted">HH не вернул доступные группы откликов.</p>}</fieldset><Button disabled={busy || !collectionIds.length} onClick={() => void importResponses()}>{busy ? 'Подождите…' : 'Импортировать выбранные'}</Button></>}
          <p className="rp-muted rp-help">Импорт создаёт профиль из доступного CV. Аккаунт и согласие кандидат оформляет сам.</p>
          {imported.length > 0 && <div className="rp-hh-people">{imported.map(n => <button key={n.candidate_id} onClick={() => onCandidate(n.candidate_id)}><span><strong>{name(n.candidate_id)}</strong><small>{n.cv_status === 'available' ? 'Профиль из HH' : 'HH не предоставил полный CV'}</small></span><ArrowUpRight size={16}/></button>)}</div>}
        </section>
        <section className="rp-card">
          <h3>Автоматические ссылки</h3><p className="rp-muted">Только импортированным кандидатам этой вакансии. Для новых назначений или завершений после включения правила.</p>
          {!state.sendingEnabled && <p className="rp-muted">Отправка на сервере выключена. Можно подготовить правило.</p>}
          {state.rules.length > 0 && <div className="rp-hh-rules">{state.rules.filter((rule, i, all) => all.findIndex(r => r.event_type === rule.event_type) === i).map(rule => <div key={rule.id}><div><strong>{eventLabels[rule.event_type] || rule.event_type}</strong><small>{rule.enabled ? 'Включено' : 'Выключено'}</small><p>{rule.template}</p></div><Button variant="ghost" disabled={busy} onClick={() => { setEvent(rule.event_type); setTemplate(rule.template); setEnabled(false); }}>Изменить</Button>{rule.enabled && <Button variant="secondary" disabled={busy} onClick={() => void act(() => recruiterRequest('hh/rules/disable', token, { ruleId: rule.id }), 'Правило выключено.')}>Выключить</Button>}</div>)}</div>}
          <form className="rp-hh-rule-form" onSubmit={saveRule}>
            <label className="rp-hh-label">Когда отправлять<select required value={event} onChange={e => setEvent(e.target.value)}><option value="">Выберите событие</option>{Object.entries(eventLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
            <label className="rp-hh-label">Сообщение в HH<textarea required rows={4} maxLength={16000} value={template} onChange={e => setTemplate(e.target.value)} placeholder="Напишите сообщение. Вставьте {invite_url} там, где должна быть ссылка."/></label>
            <label className="rp-check"><input type="checkbox" disabled={!state.sendingEnabled} checked={enabled} onChange={e => setEnabled(e.target.checked)}/><span>Включить автоматическую отправку этого сообщения</span></label>
            <Button variant={enabled ? 'default' : 'secondary'} disabled={busy || !event || !template.includes('{invite_url}')}>{enabled ? 'Сохранить и включить' : 'Сохранить выключенным'}</Button>
          </form>
        </section>
        {(state.deliveries.length > 0 || state.skipped.length > 0) && <section className="rp-card"><h3>Отправка ссылок</h3><div className="rp-hh-deliveries">{state.deliveries.map(item => <div key={item.id}><button onClick={() => onCandidate(item.candidate_id)}>{name(item.candidate_id)}</button><span>{deliveryLabels[item.status] || 'Нужна проверка'}</span></div>)}{state.skipped.map((item, index) => <div key={item.candidate_id + index}><button onClick={() => onCandidate(item.candidate_id)}>{name(item.candidate_id)}</button><span>{skipLabels[item.outcome] || 'Отправка пропущена'}</span></div>)}</div></section>}
      </>}
    </>}
  </div>;
}
