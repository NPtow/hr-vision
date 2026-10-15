import { useEffect, useId, useRef, useState } from 'react';
import { Button } from '../../components/ui/button';
import { Textarea } from '../../components/ui/textarea';
import { EmployerMeetingTimePicker } from './EmployerMeetingTimePicker';
import type { EmployerCandidateDecision, EmployerCandidateMeetingOptions, EmployerCandidateResponseValues } from './employer-candidate-profile-types';

export type EmployerCandidateResponseProps = {
  onRespond?: (values: EmployerCandidateResponseValues) => Promise<boolean>;
  decision?: EmployerCandidateDecision | null;
  meetingOptions?: EmployerCandidateMeetingOptions;
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
export function EmployerCandidateResponse({ onRespond, decision, meetingOptions, workflowRevision, busy,
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
  const [mode, setMode] = useState<'invite' | 'reject' | null>(null);
  const [meetingStart, setMeetingStart] = useState('');
  const [draft, setDraft] = useState(emptyFeedback);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [completed, setCompleted] = useState<EmployerCandidateDecision | null>(null);
  const saved = completed && (!decision || (completed.meetingStart && !decision.meetingStart)) ? completed : decision;
  const hasFeedback = saved?.decision === 'reject' && !!Object.values(saved.feedback || {}).some(Boolean);
  const canAddTime = saved?.decision === 'invite' && !saved.meetingStart && !!onRespond && !!meetingOptions;
  const editable = !saved || canAddTime;
  const meetingLabels = { draft: 'Для согласования', pending: 'Ждём подтверждения', confirmed: 'Встреча подтверждена',
    live: 'Идёт встреча', completed: 'Встреча завершена', cancelled: 'Встреча отменена', declined: 'Встреча отклонена' };

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; session.current = null; };
  }, []);
  useEffect(() => {
    if (open && !mode && !saved) invite.current?.focus();
  }, [open, mode, saved]);

  function close() {
    if (pending.current) return;
    setOpen(false); setMode(null); setMeetingStart(''); setDraft(emptyFeedback); setError('');
    session.current = null;
    requestAnimationFrame(() => { if (mounted.current) (trigger.current || status.current)?.focus(); });
  }

  function toggle() {
    if (open) { close(); return; }
    if (editable && onRespond) session.current = {
      submit: onRespond, revision: workflowRevision, requestId: crypto.randomUUID(), attemptedPayload: null,
    };
    if (canAddTime) setMode('invite');
    setError(''); setOpen(true);
  }

  async function respond(kind: EmployerCandidateResponseValues['decision']) {
    const snapshot = session.current;
    if (!snapshot || pending.current || busy || (kind === 'reject' && !draft.reason.trim())) return;
    if (kind === 'invite' && meetingOptions && !meetingStart) return;
    const feedback = kind === 'reject'
      ? { reason: draft.reason.trim(), missing: draft.missing.trim(), improve: draft.improve.trim() }
      : emptyFeedback;
    const invitation = kind === 'invite' && meetingStart ? { meetingStart } : {};
    const payload = JSON.stringify({ decision: kind, ...feedback, ...invitation });
    // A retry retains its key. Edited payloads get a new key, with the original revision.
    if (snapshot.attemptedPayload !== null && snapshot.attemptedPayload !== payload) snapshot.requestId = crypto.randomUUID();
    snapshot.attemptedPayload = payload;
    pending.current = true; setSaving(true); setError('');
    try {
      const accepted = await snapshot.submit({ decision: kind, ...feedback, ...invitation, expectedRevision: snapshot.revision, requestId: snapshot.requestId });
      if (!mounted.current || session.current !== snapshot) return;
      if (accepted) {
        setCompleted({ decision: kind, ...(kind === 'reject' ? { feedback } : {}), ...invitation,
          ...(invitation.meetingStart ? { meetingStatus: meetingOptions?.mode === 'proposal' ? 'draft' as const : 'pending' as const } : {}) });
        setOpen(false); setMode(null); setDraft(emptyFeedback); session.current = null;
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
    {(saved?.decision === 'invite' && !canAddTime) || (saved?.decision === 'reject' && !hasFeedback)
      ? <span ref={status} className="ecp-response-status" role="status" tabIndex={-1}>{saved.meetingStart ? <>
        <time dateTime={saved.meetingStart}>{new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(saved.meetingStart))} МСК</time>
        {saved.meetingStatus && <small>{meetingLabels[saved.meetingStatus]}</small>}
      </> : saved.decision === 'invite' ? 'Выбран для встречи' : 'Отказ'}</span>
      : <Button ref={trigger} className="ecp-response-trigger" type="button" variant="secondary" aria-expanded={open}
        aria-controls={panelId} disabled={saving || (editable && busy)} onClick={toggle}>{canAddTime ? 'Выбрать время' : saved ? 'Отказ' : 'Ответить'}</Button>}
    <div id={panelId} className="ecp-response-panel" hidden={!open} aria-busy={saving} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
    }}>
      {!editable ? <>
        {hasFeedback && saved && <dl className="ecp-response-feedback">{(['reason', 'missing', 'improve'] as const).map((key, index) => saved.feedback?.[key] && <div key={key}>
          <dt>{rejectQuestions[index]}</dt><dd>{saved.feedback[key]}</dd>
        </div>)}</dl>}
        <Button type="button" variant="ghost" className="ecp-response-close" onClick={close}>Свернуть</Button>
      </> : <>
        {!saved && <div className="ecp-response-choices" aria-label="Решение по кандидату">
          <Button ref={invite} type="button" variant="secondary" aria-pressed={mode === 'invite'} disabled={busy || saving}
            onClick={() => { setMode('invite'); setError(''); }}>Позвать на встречу</Button>
          <Button type="button" variant="ghost" aria-pressed={mode === 'reject'} disabled={busy || saving} onClick={() => { setMode('reject'); setError(''); }}>Отказать</Button>
          {!mode && <Button type="button" variant="ghost" disabled={saving} onClick={close}>Отмена</Button>}
        </div>}
        {mode === 'invite' && <form className="ecp-invitation" aria-label="Время встречи" onSubmit={event => { event.preventDefault(); void respond('invite'); }}>
          {meetingOptions && <EmployerMeetingTimePicker options={meetingOptions} value={meetingStart} onChange={setMeetingStart} disabled={busy || saving}/>}
          <div className="ecp-form-actions"><Button type="submit" disabled={busy || saving || (!!meetingOptions && !meetingStart)}>
            {saving ? 'Сохраняем…' : meetingOptions?.mode === 'proposal' ? 'Сохранить время' : meetingOptions ? 'Предложить время' : 'Подтвердить выбор'}
          </Button><Button type="button" variant="ghost" disabled={saving} onClick={close}>Отмена</Button></div>
        </form>}
        {mode === 'reject' && <form className="ecp-rejection" aria-label="Обратная связь при отказе" onSubmit={event => { event.preventDefault(); void respond('reject'); }}>
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
