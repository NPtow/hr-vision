import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { ArrowUp, Check, ChevronDown, MessageCircle } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Textarea } from '../../components/ui/textarea';
import { EmployerResumeDialog } from './EmployerResumeDialog';
import { EmployerCandidateResponse } from './EmployerCandidateResponse';
import type { EmployerCandidateDecision, EmployerCandidateMeetingOptions, EmployerCandidateProfileSource, EmployerCandidateResponseValues, EmployerCandidateResume, ScreeningComment } from './employer-candidate-profile-types';
import './employer-candidate-profile.css';
import './candidate-review-presentation.css';

export type EmployerCandidateProfileProps = {
  person: { id: string; name: string; role?: string; photo?: string | null };
  source: EmployerCandidateProfileSource; interview: ReactNode | ((header: ReactNode) => ReactNode); resume?: EmployerCandidateResume;
  onRespond?: (values: EmployerCandidateResponseValues) => Promise<boolean>;
  decision?: EmployerCandidateDecision | null;
  meetingOptions?: EmployerCandidateMeetingOptions;
  responseIdentity?: string;
  workflowRevision: number; busy?: boolean; error?: string;
  rejectQuestions?: readonly [string, string, string];
};

/** Review-stage profile only. The permanent stage history is a separate surface. */
export function EmployerCandidateProfile(props: EmployerCandidateProfileProps) {
  // Remount local drafts on candidate identity change, including imported intake rows.
  return <CandidateProfile key={props.person.id} {...props}/>;
}

function CandidateProfile({ person, source, interview, resume, onRespond, decision, meetingOptions, responseIdentity,
  workflowRevision, busy, error, rejectQuestions }: EmployerCandidateProfileProps) {
  const headingId = useId();
  const hasResume = !!resume || !!source.cv;
  const contacts = source.contacts?.map(contact => contact.value).filter(Boolean).join(' · ') || source.contact;
  const score = typeof source.score === 'number' && Number.isFinite(source.score) ? source.score : null;
  const inPlayer = typeof interview === 'function';
  const identity = <div className="ecp-video-identity"><h2>{person.name}</h2>
    {hasResume && <EmployerResumeDialog resume={resume} text={source.cv} busy={busy} error={error}/>}
  </div>;
  return <article className={`ecp-profile${inPlayer ? ' ecp-profile-overlay' : ''}`} aria-label={person.name} data-candidate-id={person.id}>
    {!inPlayer && <header className="ecp-heading"><h2 id={headingId}>{person.name}</h2>
      {hasResume && <EmployerResumeDialog resume={resume} text={source.cv} busy={busy} error={error}/>}
    </header>}
    <div className="ecp-interview">{inPlayer ? interview(identity) : interview}</div>
    <div className="ecp-review-footer">
      <div className="ecp-meta">{contacts && <p>{contacts}</p>}{score !== null && <p className="ecp-recruiter-score">Оценка рекрутера · {new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(score)} / 5</p>}</div>
      <EmployerCandidateResponse key={responseIdentity || person.id} onRespond={onRespond} decision={decision}
        workflowRevision={workflowRevision} busy={busy} rejectQuestions={rejectQuestions} meetingOptions={meetingOptions}/>
    </div>
    {error && <p className="ecp-error" role="alert">{error}</p>}
  </article>;
}

export type EmployerTimecodeCommentProps = {
  comments: Pick<ScreeningComment, 'id' | 'seconds' | 'text' | 'createdAt'>[];
  onSave?: (text: string, expectedRevision: number, requestId: string) => Promise<boolean>;
  workflowRevision: number; busy?: boolean; initiallyOpen?: boolean; onClose?: () => void;
  seconds?: number; openRequest?: number;
  appearance?: 'player';
};

/** Append-only comments. Capture revision and request identity before typing starts. */
export function EmployerTimecodeComment({ comments, onSave, workflowRevision, busy, initiallyOpen = false, onClose, seconds, appearance, openRequest = 0 }: EmployerTimecodeCommentProps) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const pending = useRef(false);
  const [open, setOpen] = useState(initiallyOpen);
  const [draft, setDraft] = useState(() => ({ text: '', expectedRevision: workflowRevision, requestId: crypto.randomUUID() }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (openRequest) setOpen(true); }, [openRequest]);
  const time = seconds === undefined ? '' : `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  const label = time ? `Комментарий к ${time}` : 'Комментарий к таймкоду';
  const close = () => { setOpen(false); if (appearance !== 'player') onClose?.(); requestAnimationFrame(() => trigger.current?.focus()); };
  async function save() {
    if (!onSave || pending.current || busy || !draft.text.trim()) return;
    pending.current = true; setSaving(true); setError('');
    try {
      if (await onSave(draft.text.trim(), draft.expectedRevision, draft.requestId)) {
        setDraft({ text: '', expectedRevision: workflowRevision, requestId: crypto.randomUUID() }); close();
        if (appearance === 'player') onClose?.();
      } else setError('Не удалось сохранить. Комментарий остался в поле.');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сохранить комментарий.'); }
    finally { pending.current = false; setSaving(false); }
  }
  if (!onSave && !comments.length) return null;
  if (appearance === 'player' && open && onSave) return <form className="ecp-player-comment" onSubmit={event => { event.preventDefault(); void save(); }}>
    <div className="ecp-player-comment-top"><span><Check size={12}/>{time}</span><button type="button" aria-label="Свернуть комментарий" disabled={saving} onClick={close}><ChevronDown size={17}/></button></div>
    <textarea autoFocus aria-label={label} required maxLength={4000} placeholder="Написать комментарий…" value={draft.text} disabled={busy || saving}
      onChange={event => setDraft({ ...draft, text: event.target.value })} onKeyDown={event => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); void save(); } }}/>
    {error && <p className="ecp-error" role="alert">{error}</p>}
    <div className="ecp-player-comment-bottom"><span>{saving ? 'Сохраняем…' : '⌘ ↵'}</span><button type="submit" aria-label="Сохранить комментарий" disabled={busy || saving || !draft.text.trim()}><ArrowUp size={19}/></button></div>
  </form>;
  if (appearance === 'player' && !open && onSave) return <Button className="ecp-comment-trigger" ref={trigger} type="button" variant="ghost" aria-expanded="false" onClick={() => {
    if (!draft.text.trim()) setDraft({ text: '', expectedRevision: workflowRevision, requestId: crypto.randomUUID() });
    setError(''); setOpen(true);
  }}><MessageCircle size={16}/>{draft.text.trim() ? 'Продолжить комментарий' : 'Написать комментарий'}</Button>;
  if (!open) return <Button className="ecp-comment-trigger" ref={trigger} type="button" variant="ghost" aria-label={`${label}${comments.length ? `, комментариев: ${comments.length}` : ''}`} aria-expanded="false" aria-controls={id} disabled={busy} onClick={() => {
    setDraft({ text: '', expectedRevision: workflowRevision, requestId: crypto.randomUUID() }); setError(''); setOpen(true);
  }}><MessageCircle size={14} aria-hidden="true"/>{comments.length ? `Комментарии · ${comments.length}` : 'Комментарий'}</Button>;
  return <div className="ecp-comments" id={id}>
    {!!comments.length && <ol aria-label="Комментарии к таймкоду">{comments.map(comment => <li key={comment.id}><p>{comment.text}</p><time dateTime={comment.createdAt}>{new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(comment.createdAt))} МСК</time></li>)}</ol>}
    {onSave && <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <label htmlFor={`${id}-text`}>{label}<Textarea autoFocus id={`${id}-text`} required maxLength={4000} value={draft.text} disabled={busy || saving} onChange={event => setDraft({ ...draft, text: event.target.value })}/></label>
      {error && <p className="ecp-error" role="alert">{error}</p>}
      <div className="ecp-form-actions"><Button type="submit" disabled={busy || saving || !draft.text.trim()}>{saving ? 'Сохраняем…' : 'Сохранить комментарий'}</Button><Button type="button" variant="ghost" disabled={saving} onClick={close}>Отмена</Button></div>
    </form>}
    {!onSave && <Button type="button" variant="ghost" onClick={close}>Свернуть</Button>}
  </div>;
}
