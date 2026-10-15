import { useEffect, useId, useRef, useState } from 'react';
import { Button } from '../../components/ui/button';
import { Textarea } from '../../components/ui/textarea';
import type { EmployerCandidateDecision, EmployerCandidateResponseValues } from './employer-candidate-profile-types';

export type EmployerCandidateResponseProps = {
  onRespond?: (values: EmployerCandidateResponseValues) => Promise<boolean>;
  decision?: EmployerCandidateDecision | null;
  workflowRevision: number;
  busy?: boolean;
  rejectQuestions?: readonly [string, string, string];
};

export const employerRejectionQuestions = [
  'Что не подошло?',
  'Какого опыта или навыков не хватило?',
  'Что стоит изменить в следующих кандидатах?',
] as const;

const emptyFeedback = { reason: '', missing: '', improve: '' };

/** Only the response region updates; recording and resume remain mounted. */
export function EmployerCandidateResponse({ onRespond, decision, workflowRevision, busy,
  rejectQuestions = employerRejectionQuestions }: EmployerCandidateResponseProps) {
  const panelId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const status = useRef<HTMLSpanElement>(null);
  const invite = useRef<HTMLButtonElement>(null);
  const mounted = useRef(true);
  const pending = useRef(false);
  const session = useRef<{
    submit: NonNullable<EmployerCandidateResponseProps['onRespond']>;
    revision: number; requestId: string; attemptedPayload: string | null;
  } | null>(null);
  const [open, setOpen] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [draft, setDraft] = useState(emptyFeedback);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [completed, setCompleted] = useState<EmployerCandidateDecision | null>(null);
  const saved = decision || completed;
  const hasFeedback = saved?.decision === 'reject' && !!Object.values(saved.feedback || {}).some(Boolean);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; session.current = null; };
  }, []);
  useEffect(() => {
    if (open && !rejecting && !saved) invite.current?.focus();
  }, [open, rejecting, saved]);

  function close() {
    if (pending.current) return;
    setOpen(false); setRejecting(false); setDraft(emptyFeedback); setError('');
    session.current = null;
    requestAnimationFrame(() => { if (mounted.current) (trigger.current || status.current)?.focus(); });
  }

  function toggle() {
    if (open) { close(); return; }
    if (!saved && onRespond) session.current = {
      submit: onRespond, revision: workflowRevision, requestId: crypto.randomUUID(), attemptedPayload: null,
    };
    setError(''); setOpen(true);
  }

  async function respond(kind: EmployerCandidateResponseValues['decision']) {
    const snapshot = session.current;
    if (!snapshot || pending.current || busy || (kind === 'reject' && !draft.reason.trim())) return;
    const feedback = kind === 'reject'
      ? { reason: draft.reason.trim(), missing: draft.missing.trim(), improve: draft.improve.trim() }
      : emptyFeedback;
    const payload = JSON.stringify({ decision: kind, ...feedback });
    // A retry retains its key. Edited payloads get a new key, with the original revision.
    if (snapshot.attemptedPayload !== null && snapshot.attemptedPayload !== payload) snapshot.requestId = crypto.randomUUID();
    snapshot.attemptedPayload = payload;
    pending.current = true; setSaving(true); setError('');
    try {
      const accepted = await snapshot.submit({ decision: kind, ...feedback, expectedRevision: snapshot.revision, requestId: snapshot.requestId });
      if (!mounted.current || session.current !== snapshot) return;
      if (accepted) {
        setCompleted({ decision: kind, ...(kind === 'reject' ? { feedback } : {}) });
        setOpen(false); setRejecting(false); setDraft(emptyFeedback); session.current = null;
        requestAnimationFrame(() => { if (mounted.current) (trigger.current || status.current)?.focus(); });
      } else setError('Не удалось сохранить ответ. Попробуйте ещё раз.');
    } catch (cause) {
      if (mounted.current && session.current === snapshot) setError(cause instanceof Error ? cause.message : 'Не удалось сохранить ответ.');
    } finally {
      pending.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  if (!onRespond && !saved) return null;
  return <>
    {saved?.decision === 'invite' || (saved?.decision === 'reject' && !hasFeedback)
      ? <span ref={status} className="ecp-response-status" role="status" tabIndex={-1}>{saved.decision === 'invite' ? 'Выбран для встречи' : 'Отказ'}</span>
      : <Button ref={trigger} className="ecp-response-trigger" type="button" variant="secondary" aria-expanded={open}
        aria-controls={panelId} disabled={saving || (!saved && busy)} onClick={toggle}>{saved ? 'Отказ' : 'Ответить'}</Button>}
    <div id={panelId} className="ecp-response-panel" hidden={!open} aria-busy={saving} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
    }}>
      {saved ? <>
        {hasFeedback && <dl className="ecp-response-feedback">{(['reason', 'missing', 'improve'] as const).map((key, index) => saved.feedback?.[key] && <div key={key}>
          <dt>{rejectQuestions[index]}</dt><dd>{saved.feedback[key]}</dd>
        </div>)}</dl>}
        <Button type="button" variant="ghost" className="ecp-response-close" onClick={close}>Свернуть</Button>
      </> : <>
        <div className="ecp-response-choices" aria-label="Решение по кандидату">
          <Button ref={invite} type="button" variant="secondary" disabled={busy || saving} onClick={() => void respond('invite')}>Позвать на встречу</Button>
          <Button type="button" variant="ghost" aria-pressed={rejecting} disabled={busy || saving} onClick={() => { setRejecting(true); setError(''); }}>Отказать</Button>
          {!rejecting && <Button type="button" variant="ghost" disabled={saving} onClick={close}>Отмена</Button>}
        </div>
        {rejecting && <form className="ecp-rejection" aria-label="Обратная связь при отказе" onSubmit={event => { event.preventDefault(); void respond('reject'); }}>
          {(['reason', 'missing', 'improve'] as const).map((key, index) => <label key={key} htmlFor={`${panelId}-${key}`}>{rejectQuestions[index]}
            <Textarea id={`${panelId}-${key}`} autoFocus={index === 0} required={key === 'reason'} maxLength={4000} value={draft[key]}
              disabled={busy || saving} aria-describedby={error ? `${panelId}-error` : undefined}
              onChange={event => setDraft(value => ({ ...value, [key]: event.target.value }))}/>
          </label>)}
          <div className="ecp-form-actions"><Button type="submit" disabled={busy || saving || !draft.reason.trim()}>{saving ? 'Сохраняем…' : 'Сохранить отказ'}</Button><Button type="button" variant="ghost" disabled={saving} onClick={close}>Отмена</Button></div>
        </form>}
        {error && <p id={`${panelId}-error`} className="ecp-error" role="alert">{error}</p>}
      </>}
    </div>
  </>;
}
