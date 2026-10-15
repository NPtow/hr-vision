import { useId, useRef, useState, type ReactNode } from 'react';
import { MessageCircle } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Textarea } from '../../components/ui/textarea';
import { EmployerResumePreview } from './EmployerResumePreview';
import { EmployerCandidateResponse } from './EmployerCandidateResponse';
import type { EmployerCandidateDecision, EmployerCandidateProfileSource, EmployerCandidateResponseValues, EmployerCandidateResume, ScreeningComment } from './employer-candidate-profile-types';
import fileIcon from './assets/candidate-profile/file.svg';
import downloadIcon from './assets/candidate-profile/download.svg';
import './employer-candidate-profile.css';

export type EmployerCandidateProfileProps = {
  person: { id: string; name: string; role?: string; photo?: string | null };
  source: EmployerCandidateProfileSource; interview: ReactNode; resume?: EmployerCandidateResume;
  onRespond?: (values: EmployerCandidateResponseValues) => Promise<boolean>;
  decision?: EmployerCandidateDecision | null;
  responseIdentity?: string;
  workflowRevision: number; busy?: boolean; error?: string;
  rejectQuestions?: readonly [string, string, string];
};

/** Review-stage profile only. The permanent stage history is a separate surface. */
export function EmployerCandidateProfile(props: EmployerCandidateProfileProps) {
  // Remount local drafts on candidate identity change, including imported intake rows.
  return <CandidateProfile key={props.person.id} {...props}/>;
}

function CandidateProfile({ person, source, interview, resume, onRespond, decision, responseIdentity,
  workflowRevision, busy, error, rejectQuestions }: EmployerCandidateProfileProps) {
  const headingId = useId();
  const profileId = useId();
  const [profileOpen, setProfileOpen] = useState(false);
  const hasResume = !!resume || !!source.cv;
  const contacts = source.contacts?.map(contact => contact.value).filter(Boolean).join(' · ') || source.contact;
  const score = typeof source.score === 'number' && Number.isFinite(source.score) ? source.score : null;
  return <article className="ecp-profile" aria-labelledby={headingId} data-candidate-id={person.id}>
    <header className="ecp-heading">
      <h2 id={headingId}>{person.name}{score !== null && <span className="ecp-score"> · {new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(score)}</span>}</h2>
      <EmployerCandidateResponse key={responseIdentity || person.id} onRespond={onRespond} decision={decision}
        workflowRevision={workflowRevision} busy={busy} rejectQuestions={rejectQuestions}/>
    </header>
    {contacts && <div className="ecp-meta"><p>{contacts}</p></div>}
    {hasResume && <div className="ecp-resume">
      <span className="ecp-file-icon"><img src={fileIcon} alt=""/></span>
      <div className="ecp-file-copy"><strong>{resume?.name || 'Резюме'}</strong>{resume?.sizeLabel && <span>{resume.sizeLabel}</span>}</div>
      <div className="ecp-file-actions">
        {resume?.onDownload && <Button variant="ghost" size="icon" aria-label="Скачать резюме" onClick={resume.onDownload}><img src={downloadIcon} alt=""/></Button>}
        <Button variant="secondary" aria-expanded={profileOpen} aria-controls={profileId} onClick={() => setProfileOpen(value => !value)}>{profileOpen ? 'Свернуть' : 'Открыть'}</Button>
      </div>
    </div>}
    {profileOpen && <section className="ecp-resume-preview" id={profileId} aria-label="Резюме">
      <EmployerResumePreview key={resume?.id || 'source-text'} resume={resume} text={source.cv}/>
    </section>}
    {error && <p className="ecp-error" role="alert">{error}</p>}
    <div className="ecp-interview">{interview}</div>
  </article>;
}

export type EmployerTimecodeCommentProps = {
  comments: Pick<ScreeningComment, 'id' | 'seconds' | 'text' | 'createdAt'>[];
  onSave?: (text: string, expectedRevision: number, requestId: string) => Promise<boolean>;
  workflowRevision: number; busy?: boolean; initiallyOpen?: boolean; onClose?: () => void;
  seconds?: number;
};

/** Append-only comments. Capture revision and request identity before typing starts. */
export function EmployerTimecodeComment({ comments, onSave, workflowRevision, busy, initiallyOpen = false, onClose, seconds }: EmployerTimecodeCommentProps) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const pending = useRef(false);
  const [open, setOpen] = useState(initiallyOpen);
  const [draft, setDraft] = useState(() => ({ text: '', expectedRevision: workflowRevision, requestId: crypto.randomUUID() }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const time = seconds === undefined ? '' : `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  const label = time ? `Комментарий к ${time}` : 'Комментарий к таймкоду';
  const close = () => { setOpen(false); onClose?.(); requestAnimationFrame(() => trigger.current?.focus()); };
  if (!onSave && !comments.length) return null;
  if (!open) return <Button className="ecp-comment-trigger" ref={trigger} type="button" variant="ghost" aria-label={`${label}${comments.length ? `, комментариев: ${comments.length}` : ''}`} aria-expanded="false" aria-controls={id} disabled={busy} onClick={() => {
    setDraft({ text: '', expectedRevision: workflowRevision, requestId: crypto.randomUUID() }); setError(''); setOpen(true);
  }}><MessageCircle size={14} aria-hidden="true"/>{comments.length ? `Комментарии · ${comments.length}` : 'Комментарий'}</Button>;
  return <div className="ecp-comments" id={id}>
    {!!comments.length && <ol aria-label="Комментарии к таймкоду">{comments.map(comment => <li key={comment.id}><p>{comment.text}</p><time dateTime={comment.createdAt}>{new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(comment.createdAt))} МСК</time></li>)}</ol>}
    {onSave && <form onSubmit={async event => {
      event.preventDefault(); if (pending.current || busy || !draft.text.trim()) return;
      pending.current = true; setSaving(true); setError('');
      try { if (await onSave(draft.text.trim(), draft.expectedRevision, draft.requestId)) { setDraft({ text: '', expectedRevision: workflowRevision, requestId: crypto.randomUUID() }); close(); } else setError('Не удалось сохранить. Комментарий остался в поле.'); }
      catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сохранить комментарий.'); }
      finally { pending.current = false; setSaving(false); }
    }}>
      <label htmlFor={`${id}-text`}>{label}<Textarea autoFocus id={`${id}-text`} required maxLength={4000} value={draft.text} disabled={busy || saving} onChange={event => setDraft({ ...draft, text: event.target.value })}/></label>
      {error && <p className="ecp-error" role="alert">{error}</p>}
      <div className="ecp-form-actions"><Button type="submit" disabled={busy || saving || !draft.text.trim()}>{saving ? 'Сохраняем…' : 'Сохранить комментарий'}</Button><Button type="button" variant="ghost" disabled={saving} onClick={close}>Отмена</Button></div>
    </form>}
    {!onSave && <Button type="button" variant="ghost" onClick={close}>Свернуть</Button>}
  </div>;
}
