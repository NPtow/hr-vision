import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowUpRight, Check, ChevronRight, ListFilter, Search, Send, Table2, X } from 'lucide-react';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { useOperatorSession } from '../hh-outreach/session';
import { OutreachError } from '../hh-outreach/api';
import { selectionRequest, verdicts, states, reasons, criterionNames, type Directory, type SelectionPerson, type SelectionVacancy } from './api';
import './selection-workbench.css';

type Tab = 'candidates' | 'vacancies' | 'assessments';
type StatusChoice = 'all' | 'yes' | 'no';
type StatusFilters = { interview: StatusChoice; telegram: StatusChoice; email: StatusChoice };
const emptyStatusFilters: StatusFilters = { interview: 'all', telegram: 'all', email: 'all' };
const caption = { candidates: 'Кандидаты', vacancies: 'Вакансии', assessments: 'Оценки' };
const originLink = (workspace: string) => {
  const url = new URL(location.href); url.searchParams.set('workspace', workspace);
  url.searchParams.delete('vacancyId'); url.searchParams.delete('tab'); return url.pathname + url.search;
};

export function SelectionWorkbench() {
  const auth = useOperatorSession();
  return <main className="sl-root"><header className="sl-topbar"><img src={logo} alt="HR Vision"/><span>Подборки</span><a href={originLink('outreach')}>Рассылка<ArrowUpRight size={15}/></a></header>
    {auth.status === 'ready' ? <Workbench key={auth.token} token={auth.token} expire={auth.expire}/> : <section className="sl-access"><h1>{auth.status === 'checking' ? 'Проверяем доступ…' : 'Вход для команды'}</h1>{auth.status !== 'checking' && <><p>{auth.error || 'Откройте рабочую ссылку команды HR Vision.'}</p><button onClick={() => void auth.check()}>Проверить доступ</button></>}</section>}
  </main>;
}

function Workbench({ token, expire }: { token: string; expire: () => void }) {
  const query = new URLSearchParams(location.search);
  const [tab, setTab] = useState<Tab>(query.get('tab') === 'assessments' ? 'assessments' : query.get('tab') === 'vacancies' ? 'vacancies' : 'candidates');
  const [vacancies, setVacancies] = useState<SelectionVacancy[]>([]);
  const [vid, setVid] = useState(query.get('vacancyId') || '');
  const [people, setPeople] = useState<SelectionPerson[]>([]);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'all' | 'cohort' | 'ready'>('cohort');
  const [statusFilters, setStatusFilters] = useState<StatusFilters>(emptyStatusFilters);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState('');
  const [busy, setBusy] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirm, setConfirm] = useState(false);
  const generation = useRef(0);
  const pending = useRef(false);
  const command = useRef<{ key: string; ids: string[]; vid: string } | null>(null);
  const load = useCallback(async () => {
    const seq = ++generation.current;
    try {
      const list = await selectionRequest<{ vacancies: SelectionVacancy[] }>('vacancies', token);
      const target = list.vacancies.some(v => v.id === vid) ? vid : list.vacancies[0]?.id || '';
      const data = await selectionRequest<Directory>('candidates' + (target ? '?vacancyId=' + encodeURIComponent(target) : ''), token);
      if (seq !== generation.current) return;
      setVacancies(list.vacancies); setPeople(data.candidates); setVid(target); setError('');
    } catch (cause) {
      if (seq !== generation.current) return;
      if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire();
      else setError((cause as Error).message);
    } finally { if (seq === generation.current) setLoading(false); }
  }, [token, vid, expire]);
  useEffect(() => { setLoading(true); void load(); return () => { generation.current++; }; }, [load]);
  useEffect(() => {
    const url = new URL(location.href); url.searchParams.set('tab', tab);
    if (vid) url.searchParams.set('vacancyId', vid); else url.searchParams.delete('vacancyId');
    history.replaceState(history.state, '', url);
  }, [tab, vid]);
  useEffect(() => { setSelected(new Set()); setDetail(''); setConfirm(false); command.current = null; }, [vid, tab]);
  const vacancy = vacancies.find(v => v.id === vid);
  const hasCohort = people.some(p => p.inCohort);
  const statusFilterCount = Object.values(statusFilters).filter(value => value !== 'all').length;
  const rows = useMemo(() => people.filter(p => (tab !== 'assessments' || p.assessment || p.inCohort)
    && (filter !== 'cohort' || !hasCohort || p.inCohort) && (filter !== 'ready' || p.canAdd)
    && (statusFilters.interview === 'all' || (p.interviewStatus === 'completed') === (statusFilters.interview === 'yes'))
    && (statusFilters.telegram === 'all' || p.telegramConnected === (statusFilters.telegram === 'yes'))
    && (statusFilters.email === 'all' || p.emailConnected === (statusFilters.email === 'yes'))
    && [p.name, p.desiredRole, p.city, p.email].join(' ').toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru'))), [people, tab, search, filter, hasCohort, statusFilters]);
  const checked = rows.filter(p => selected.has(p.id));
  const canPublish = checked.length > 0 && checked.every(p => p.canAdd);
  const person = people.find(p => p.id === detail);

  async function run(label: string, action: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true; setBusy(label); setError(''); setNotice('');
    try { await action(); await load(); }
    catch (cause) {
      if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire();
      else setError((cause as Error).message);
    } finally { pending.current = false; setBusy(''); }
  }
  async function save(p: SelectionPerson, fields: Partial<Pick<SelectionPerson, 'name' | 'desiredRole' | 'city' | 'about'>>) {
    await run('Сохраняем', async () => {
      await selectionRequest('candidates/update', token, { candidateId: p.id, revision: p.revision, fields });
      setNotice('Изменения сохранены');
    });
  }
  async function publish() {
    if (!canPublish || !vid) return;
    const ids = checked.map(p => p.id).sort();
    if (!command.current || command.current.vid !== vid || command.current.ids.join() !== ids.join()) command.current = { key: crypto.randomUUID(), ids, vid };
    const current = command.current;
    await run('Добавляем', async () => {
      const receipt = await selectionRequest<{ added: string[]; waitingProfile: string[]; proposed: string[] }>('publish', token, { vacancyId: vid, candidateIds: current.ids, requestId: current.key });
      setConfirm(false); setSelected(new Set()); command.current = null;
      setNotice(`Добавлено: ${receipt.added.length}. Приглашения подготовлены; профиль откроется работодателю после согласования кандидатом.`);
    });
  }
  const toggle = (id: string) => setSelected(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const changeStatus = (key: keyof StatusFilters, value: StatusChoice) => {
    setStatusFilters(current => ({ ...current, [key]: value })); setSelected(new Set());
  };
  const resetStatus = () => { setStatusFilters(emptyStatusFilters); setSelected(new Set()); };
  const checkedIds = rows.map(p => p.id);
  return <div className="sl-workspace">
    <div className="sl-heading"><h1>База подбора</h1><label className="sl-vacancy-picker"><span className="sl-sr">Вакансия для подборки</span><select value={vid} disabled={!!busy || loading} onChange={e => setVid(e.target.value)}>{vacancies.length === 0 && <option value="">Нет вакансий</option>}{vacancies.map(v => <option key={v.id} value={v.id}>{v.company} · {v.title}</option>)}</select></label></div>
    <nav className="sl-tabs" aria-label="Таблицы подбора">{(Object.keys(caption) as Tab[]).map(key => <button disabled={!!busy} key={key} aria-current={key === tab ? 'page' : undefined} onClick={() => { setTab(key); setSearch(''); }}>{caption[key]}<span>{key === 'vacancies' ? vacancies.length : key === 'assessments' ? people.filter(p => p.assessment).length : people.length}</span></button>)}</nav>
    <section className="sl-surface" aria-label={caption[tab]} aria-busy={loading || !!busy}>
      <div className="sl-toolbar"><label className="sl-search"><Search size={17}/><span className="sl-sr">Поиск</span><input placeholder={tab === 'vacancies' ? 'Найти вакансию' : 'Найти кандидата'} value={search} onChange={e => setSearch(e.target.value)}/></label>
        {tab !== 'vacancies' && <><label className="sl-filter"><span className="sl-sr">Группа кандидатов</span><select value={filter} onChange={e => { setFilter(e.target.value as typeof filter); setSelected(new Set()); }}><option value="cohort">Текущая группа</option><option value="all">Все кандидаты</option><option value="ready">Готовы к добавлению</option></select></label>
          <button className="sl-status-toggle" aria-expanded={filtersOpen} aria-controls="sl-status-filters" data-active={statusFilterCount > 0} onClick={() => setFiltersOpen(!filtersOpen)}><ListFilter size={16}/>Фильтры{statusFilterCount > 0 && <span>{statusFilterCount}</span>}</button>
          <button className="sl-send" disabled={!canPublish || !!busy} onClick={() => setConfirm(true)}><Send size={16}/>Добавить{checked.length ? ` · ${checked.length}` : ''}</button></>}
        <button className="sl-refresh" disabled={!!busy || loading} onClick={() => void load()}>Обновить</button>
      </div>
      {tab !== 'vacancies' && filtersOpen && <div className="sl-status-filters" id="sl-status-filters" role="group" aria-label="Статусы кандидатов">
        <label>Интервью<select aria-label="Интервью" value={statusFilters.interview} onChange={e => changeStatus('interview', e.target.value as StatusChoice)}><option value="all">Любой статус</option><option value="yes">Проведено</option><option value="no">Не проведено</option></select></label>
        <label>Telegram<select aria-label="Telegram" value={statusFilters.telegram} onChange={e => changeStatus('telegram', e.target.value as StatusChoice)}><option value="all">Любой статус</option><option value="yes">Подключён</option><option value="no">Не подключён</option></select></label>
        <label>Почта<select aria-label="Почта" value={statusFilters.email} onChange={e => changeStatus('email', e.target.value as StatusChoice)}><option value="all">Любой статус</option><option value="yes">Подключена</option><option value="no">Не подключена</option></select></label>
        {statusFilterCount > 0 && <button className="sl-filter-reset" onClick={resetStatus}>Сбросить</button>}
      </div>}
      {error && <p className="sl-error" role="alert">{error}</p>}{notice && <p className="sl-notice" role="status"><Check size={17}/>{notice}</p>}
      {loading ? <div className="sl-empty" role="status">Загружаем данные…</div> : tab === 'vacancies' ? <div className="sl-scroll"><table><thead><tr><th>Вакансия</th><th>Работодатель</th><th>Город</th><th>Условия</th><th/></tr></thead><tbody>{vacancies.filter(v => [v.title, v.company, v.city].join(' ').toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru'))).map(v => <tr key={v.id}><td><strong>{v.title}</strong></td><td>{v.company}</td><td>{v.city || '—'}</td><td>{v.salary || '—'}</td><td><button className="sl-open" onClick={() => { setVid(v.id); setTab('assessments'); setSearch(''); }}>Открыть<ChevronRight size={15}/></button></td></tr>)}</tbody></table></div> : rows.length ? <>
        <div className="sl-scroll"><table><thead><tr><th className="sl-check"><input type="checkbox" aria-label="Выбрать видимых кандидатов" checked={rows.length > 0 && rows.every(p => selected.has(p.id))} onChange={e => setSelected(e.target.checked ? new Set(checkedIds) : new Set())}/></th><th>Кандидат</th>{tab === 'candidates' ? <><th>Роль</th><th>Город</th><th>Интервью</th><th>Telegram</th><th>Почта</th></> : <><th>Оценки</th><th>Рекомендация</th><th>Согласование</th></>}<th/></tr></thead><tbody>{rows.map(p => <tr key={p.id} data-selected={selected.has(p.id)}><td className="sl-check"><input type="checkbox" checked={selected.has(p.id)} aria-label={`Выбрать: ${p.name}`} onChange={() => toggle(p.id)}/></td><td><button className="sl-person" onClick={() => setDetail(p.id)}>{p.name || 'Без имени'}</button>{tab === 'assessments' && <small>{p.desiredRole}</small>}</td>{tab === 'candidates' ? <><td><InlineCell label={`Роль: ${p.name}`} value={p.desiredRole} disabled={!!busy} onSave={value => void save(p, { desiredRole: value })}/></td><td><InlineCell label={`Город: ${p.name}`} value={p.city} disabled={!!busy} onSave={value => void save(p, { city: value })}/></td><td><span className={'sl-status ' + (p.interviewStatus === 'completed' ? 'sl-green' : '')}>{p.interviewStatus === 'completed' ? 'Проведено' : p.interviewStatus === 'booked' ? 'Назначено' : 'Нет интервью'}</span></td><td><span className={'sl-status ' + (p.telegramConnected ? 'sl-green' : '')}>{p.telegramConnected ? 'Подключён' : 'Не подключён'}</span></td><td><span className={'sl-status ' + (p.emailConnected ? 'sl-green' : '')} title={p.emailConnected ? p.email : p.email ? 'Адрес есть в резюме; почта ещё не подключена' : 'Почта не указана'}>{p.emailConnected ? 'Подключена' : 'Не подключена'}</span></td></> : <><td>{p.assessment ? <span className="sl-score">{p.assessment.counts.supported ? `${p.assessment.counts.supported} с оценкой` : 'Без баллов'}<small>{p.assessment.counts.unknown} · недостаточно данных</small></span> : <span className="sl-muted">Нет результата</span>}</td><td><span className={'sl-status ' + (p.assessment?.recommendation === 'ranked' ? 'sl-green' : '')}>{p.assessment ? verdicts[p.assessment.recommendation] || p.assessment.recommendation : 'Ожидает материалов'}</span></td><td>{p.application ? p.application.status === 'accepted' ? 'Согласовано' : 'Ожидает ответа' : p.nomination ? p.nomination.state === 'blocked' ? 'Нужна проверка' : 'Приглашён в профиль' : p.consentReuse ? 'Можно предложить' : 'Нужно согласие'}</td></>}<td><button className="sl-icon" aria-label={`Подробнее: ${p.name}`} onClick={() => setDetail(p.id)}><ChevronRight size={18}/></button></td></tr>)}</tbody></table></div>
        <footer className="sl-table-footer"><span>{rows.length} в таблице{checked.length ? ` · выбрано ${checked.length}` : ''}</span>{checked.length > 0 && !canPublish && <span>Для добавления нужна актуальная рекомендация ядра</span>}</footer>
      </> : <div className="sl-empty"><Table2 size={24}/><h2>Ничего не найдено</h2><button onClick={() => { setFilter('all'); setSearch(''); resetStatus(); }}>Показать всех</button></div>}
    </section>
    {person && <Detail person={person} busy={!!busy} onClose={() => setDetail('')} onSave={fields => void save(person, fields)}/>}
    {confirm && <div className="sl-overlay"><ModalPanel className="sl-dialog" titleId="sl-confirm-title" onClose={() => { if (!busy) setConfirm(false); }}><button className="sl-close" aria-label="Закрыть подтверждение" disabled={!!busy} onClick={() => setConfirm(false)}><X size={20}/></button><h2 id="sl-confirm-title">Добавить в подборку?</h2><p>{vacancy?.company}<br/><strong>{vacancy?.title}</strong></p><ul>{checked.map(p => <li key={p.id}>{p.name}</li>)}</ul><p>Кандидаты получат предложение по почте и в подключённом Telegram. Работодатель увидит профиль после согласования.</p><div className="sl-dialog-actions"><button disabled={!!busy} onClick={() => setConfirm(false)}>Назад</button><button className="sl-primary" disabled={!!busy} onClick={() => void publish()}>{busy || 'Добавить и пригласить'}</button></div></ModalPanel></div>}
  </div>;
}

function InlineCell({ value, label, disabled, onSave }: { value: string; label: string; disabled: boolean; onSave: (value: string) => void }) {
  const [editing, setEditing] = useState(false); const [draft, setDraft] = useState(value);
  useEffect(() => { setDraft(value); }, [value]);
  if (!editing) return <button className="sl-cell" aria-label={`Изменить ${label}`} disabled={disabled} onClick={() => { setDraft(value); setEditing(true); }}>{value || '—'}</button>;
  return <input className="sl-cell-input" aria-label={label} autoFocus value={draft} onChange={e => setDraft(e.target.value)} onBlur={() => { setEditing(false); if (draft !== value) onSave(draft); }} onKeyDown={e => { if (e.key === 'Escape') { setDraft(value); setEditing(false); } if (e.key === 'Enter') e.currentTarget.blur(); }}/>;
}

function ModalPanel({ className, titleId, onClose, children }: { className: string; titleId: string; onClose: () => void; children: ReactNode }) {
  const panel = useRef<HTMLElement>(null);
  const close = useRef(onClose); close.current = onClose;
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close.current();
      if (e.key === 'Tab' && panel.current) {
        const items = [...panel.current.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select,a[href],textarea:not(:disabled)')];
        const first = items[0], last = items.at(-1);
        if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', key); return () => { document.removeEventListener('keydown', key); before?.focus(); };
  }, []);
  return <section ref={panel} tabIndex={-1} className={className} role="dialog" aria-modal="true" aria-labelledby={titleId}>{children}</section>;
}

function Detail({ person: p, busy, onClose, onSave }: { person: SelectionPerson; busy: boolean; onClose: () => void; onSave: (fields: Partial<Pick<SelectionPerson, 'name' | 'desiredRole' | 'city' | 'about'>>) => void }) {
  return <div className="sl-overlay sl-drawer-overlay" onClick={e => { if (e.target === e.currentTarget) onClose(); }}><ModalPanel className="sl-drawer" titleId="sl-person-title" onClose={onClose}><button className="sl-close" aria-label="Закрыть карточку" onClick={onClose}><X size={20}/></button><h2 id="sl-person-title">{p.name}</h2><p className="sl-muted">{p.desiredRole}</p><div className="sl-detail-fields"><label>Имя<InlineCell value={p.name} label="Имя" disabled={busy} onSave={name => onSave({ name })}/></label><label>Город<InlineCell value={p.city} label="Город" disabled={busy} onSave={city => onSave({ city })}/></label></div><p className="sl-about">{p.about || 'Описание пока не заполнено'}</p>
      <h3>Приглашения</h3>{p.deliveries.length ? <div>{p.deliveries.map(d => <p key={d.id}>{d.event === 'selection.invitation' ? 'Профиль · ' : 'Вакансия · '}{d.channel === 'email' ? 'Почта' : 'Telegram'}: {({ delivered: 'Принято сервисом', pending: 'В очереди', sending: 'Отправляется', disabled: 'Не настроено', unbound: 'Нет контакта', unverified: 'Адрес не подтверждён', error: 'Ошибка' } as Record<string, string>)[d.status] || d.status}</p>)}</div> : <p className="sl-muted">Ещё не отправлялись</p>}<h3>Оценка ядра</h3>{p.assessment ? <><p>{verdicts[p.assessment.recommendation] || p.assessment.recommendation}. {p.assessment.reason}</p><div className="sl-criteria"><table><thead><tr><th>Критерий</th><th>Баллы</th><th>Основание</th></tr></thead><tbody>{p.assessment.criteria.map(c => <tr key={c.criterion_id}><td title={c.criterion_id}>{criterionNames[c.criterion_id] || c.criterion_id}</td><td>{c.failed_processing ? 'Ошибка' : c.level === null ? '—' : `${c.level} / 5`}</td><td>{c.failed_processing ? 'Обработка не завершена' : states[c.state] || c.state}{c.reason_codes?.map(r => <small key={r}>{reasons[r] || r}</small>)}</td></tr>)}</tbody></table></div><details><summary>Источник и ограничения</summary><p>{p.assessment.model} · {new Date(p.assessment.createdAt).toLocaleString('ru-RU')}</p><p className="sl-hash">{p.assessment.documentHash}</p><ul>{p.assessment.limitations.map((v, i) => <li key={i}>{v.code} · {v.subject}</li>)}</ul></details></> : <p className="sl-muted">Результат ещё не получен. Баллы появятся после обработки ядром.</p>}
    </ModalPanel></div>;
}
