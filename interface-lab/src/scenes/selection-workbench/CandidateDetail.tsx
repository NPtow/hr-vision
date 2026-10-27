import { useEffect, useRef, useState, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { OutreachError } from '../hh-outreach/api';
import { selectionRequest, verdicts, states, reasons, criterionNames, type SelectionPerson, type Readiness } from './api';
import { RequiredFieldsEditor } from './RequiredFieldsEditor';
import { ReviewState } from './review-state';
import { startReadinessPolling } from './readiness-poll';

export function InlineCell({ value, label, disabled, onSave }: { value: string; label: string; disabled: boolean; onSave: (value: string) => void }) {
  const [editing, setEditing] = useState(false); const [draft, setDraft] = useState(value);
  useEffect(() => { setDraft(value); }, [value]);
  if (!editing) return <button className="sl-cell" aria-label={`Изменить ${label}`} disabled={disabled} onClick={() => { setDraft(value); setEditing(true); }}>{value || '—'}</button>;
  return <input className="sl-cell-input" aria-label={label} autoFocus value={draft} onChange={e => setDraft(e.target.value)} onBlur={() => { setEditing(false); if (draft !== value) onSave(draft); }} onKeyDown={e => { if (e.key === 'Escape') { setDraft(value); setEditing(false); } if (e.key === 'Enter') e.currentTarget.blur(); }}/>;
}

export function ModalPanel({ className, titleId, onClose, children }: { className: string; titleId: string; onClose: () => void; children: ReactNode }) {
  const panel = useRef<HTMLElement>(null);
  const close = useRef(onClose); close.current = onClose;
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close.current();
      if (e.key === 'Tab' && panel.current) {
        const items = [...panel.current.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href],textarea:not(:disabled),summary')].filter(item => item.getClientRects().length > 0);
        const first = items[0], last = items.at(-1);
        if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', key); return () => { document.removeEventListener('keydown', key); before?.focus(); };
  }, []);
  return <section ref={panel} tabIndex={-1} className={className} role="dialog" aria-modal="true" aria-labelledby={titleId}>{children}</section>;
}

export function CandidateDetail({ person: p, token, expire, busy, onClose, onSave }: {
  person: SelectionPerson; token: string; expire: () => void; busy: boolean; onClose: () => void;
  onSave: (fields: Partial<Pick<SelectionPerson, 'name' | 'desiredRole' | 'city' | 'about'>>) => void;
}) {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');
  const [pollExhausted, setPollExhausted] = useState(false);
  useEffect(() => {
    setLoading(true); setError(''); setReadiness(null); setPollExhausted(false);
    return startReadinessPolling(p.id, token, {
      onValue: value => { setReadiness(value); setLoading(false); },
      onError: cause => { setLoading(false); if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire(); else setError('Не удалось проверить материалы и оценку. Повторите проверку.'); },
      onExhausted: () => setPollExhausted(true),
    });
  }, [p.id, p.sourceRevision, token, retry, expire]);
  async function startAssessment() {
    if (starting) return;
    setStarting(true); setStartError('');
    try {
      const value = await selectionRequest<{ candidateId: string; assessment: Readiness['assessment'] }>(
        'candidates/' + encodeURIComponent(p.id) + '/assessment', token, { revision: p.revision });
      if (value.candidateId !== p.id) throw new Error('Получена карточка другого кандидата.');
      setReadiness(current => current ? { ...current, assessment: value.assessment } : current);
      setRetry(value => value + 1);
    } catch (cause) {
      if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire();
      else setStartError((cause as Error).message || 'Не удалось запустить оценку.');
    } finally { setStarting(false); }
  }
  return <div className="sl-overlay sl-drawer-overlay" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
    <ModalPanel className="sl-drawer" titleId="sl-person-title" onClose={onClose}>
      <button className="sl-close" aria-label="Закрыть карточку" onClick={onClose}><X size={20}/></button>
      <h2 id="sl-person-title">{p.name}</h2><p className="sl-muted">{p.desiredRole}</p>
      <div className="sl-detail-fields"><label>Имя<InlineCell value={p.name} label="Имя" disabled={busy} onSave={name => onSave({ name })}/></label><label>Город<InlineCell value={p.city} label="Город" disabled={busy} onSave={city => onSave({ city })}/></label></div>
      {p.about && <p className="sl-about">{p.about}</p>}
      <ReviewState person={p} readiness={readiness} loading={loading} error={error}/>
      {readiness?.assessment.canRun && <button className="sl-open" disabled={loading || starting || busy}
        onClick={() => void startAssessment()}>{starting ? 'Собираем CV и интервью…' : readiness.assessment.coreRun ? 'Обновить оценку по материалам' : 'Оценить CV и интервью'}</button>}
      {startError && <p role="alert">{startError}</p>}
      {readiness?.assessment.rubric && <p className="sl-muted">Шкала: {readiness.assessment.rubric.version} · параметров: {readiness.assessment.rubric.parameterCount}</p>}
      {pollExhausted && <p className="sl-muted" role="status">Автообновление приостановлено. Проверить статус можно кнопкой ниже.</p>}
      <button className="sl-open" disabled={loading} onClick={() => setRetry(value => value + 1)}>Проверить актуальность</button>
      {p.assessment && <details className="sl-legacy"><summary>Архивная оценка</summary>
        <p>Предыдущий результат сохранён отдельно. Это не результат нового ядра.</p>
        <p>{verdicts[p.assessment.recommendation] || 'Статус неизвестен'}. {p.assessment.reason}</p>
        <div className="sl-criteria"><table><thead><tr><th>Критерий</th><th>Баллы</th><th>Основание</th></tr></thead><tbody>{p.assessment.criteria.map(c => <tr key={c.criterion_id}>
          <td title={c.criterion_id}>{criterionNames[c.criterion_id] || c.criterion_id}</td><td>{c.failed_processing ? 'Ошибка' : c.level === null ? '—' : `${c.level} / 5`}</td>
          <td>{c.failed_processing ? 'Обработка не завершена' : states[c.state] || c.state}{c.reason_codes?.map(r => <small key={r}>{reasons[r] || r}</small>)}</td>
        </tr>)}</tbody></table></div><p>{p.assessment.model} · {new Date(p.assessment.createdAt).toLocaleString('ru-RU')}</p><p className="sl-hash">{p.assessment.documentHash}</p>
      </details>}
      <RequiredFieldsEditor kind="candidate" id={p.id} token={token} expire={expire} onSaved={() => setRetry(v => v + 1)}/>
      <h3>Приглашения</h3>{p.deliveries.length ? <div>{p.deliveries.map(d => <p key={d.id}>{d.event === 'selection.invitation' ? 'Профиль · ' : 'Вакансия · '}{d.channel === 'email' ? 'Почта' : 'Telegram'}: {({ delivered: 'Принято сервисом', pending: 'В очереди', sending: 'Отправляется', disabled: 'Не настроено', unbound: 'Нет контакта', unverified: 'Адрес не подтверждён', error: 'Ошибка' } as Record<string, string>)[d.status] || d.status}</p>)}</div> : <p className="sl-muted">Ещё не отправлялись</p>}
    </ModalPanel>
  </div>;
}
