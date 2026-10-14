import { useId, useRef, useState, type ReactNode } from 'react';
import { MessageCircle } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Textarea } from '../../components/ui/textarea';
import type { EmployerCandidateProfileSource, EmployerCandidateResume, EmployerRejectionValues, ScreeningComment } from './employer-candidate-profile-types';
import fileIcon from './assets/candidate-profile/file.svg';
import downloadIcon from './assets/candidate-profile/download.svg';
import './employer-candidate-profile.css';

export type EmployerCandidateProfileProps = {
  person: { id: string; name: string; role?: string; photo?: string | null };
  source: EmployerCandidateProfileSource; interview: ReactNode; resume?: EmployerCandidateResume;
  onMeet?: () => void; meetingUnavailableReason?: string;
  onReject?: (values: EmployerRejectionValues) => Promise<boolean>;
  workflowRevision: number; busy?: boolean; error?: string;
  rejectQuestions?: readonly [string, string, string];
  decisionFeedback?: { reason: string; missing?: string; improve?: string } | null;
};
const defaultQuestions = ['Что не подошло?', 'Какого опыта или навыков не хватило?', 'Что стоит изменить в следующих кандидатах?'] as const;

/** Review-stage profile only. The permanent stage history is a separate surface. */
export function EmployerCandidateProfile(props: EmployerCandidateProfileProps) {
  // Remount local drafts on candidate identity change, including imported intake rows.
  return <CandidateProfile key={props.person.id} {...props}/>;
}

function CandidateProfile({ person, source, interview, resume, onMeet, meetingUnavailableReason, onReject,
  workflowRevision, busy, error, rejectQuestions = defaultQuestions, decisionFeedback }: EmployerCandidateProfileProps) {
  const headingId = useId();
  const profileId = useId();
  const [profileOpen, setProfileOpen] = useState(false);
  const [rejection, setRejection] = useState<EmployerRejectionValues | null>(null);
  const [saving, setSaving] = useState(false);
  const [localError, setLocalError] = useState('');
  const rejecting = useRef(false);
  const rejectTrigger = useRef<HTMLButtonElement>(null);
  const hasProfile = !!source.cv || !!source.profile?.length;
  const contacts = source.contacts?.map(contact => contact.value).filter(Boolean).join(' · ') || source.contact;
  const score = typeof source.score === 'number' && Number.isFinite(source.score) ? source.score : null;
  const finishRejection = () => { setRejection(null); requestAnimationFrame(() => rejectTrigger.current?.focus()); };
  return <article className="ecp-profile" aria-labelledby={headingId} data-candidate-id={person.id}>
    <header className="ecp-heading">
      <h2 id={headingId}>{person.name}{score !== null && <span className="ecp-score"> · {new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(score)}</span>}</h2>
      {(onMeet || onReject) && <div className="ecp-decision" aria-label="Решение по кандидату">
        {onMeet && <Button disabled={busy || saving} onClick={onMeet}>Назначить встречу</Button>}
        {onReject && <Button ref={rejectTrigger} variant="ghost" disabled={busy || saving || !!rejection} onClick={() => {
          setLocalError(''); setRejection({ reason: '', missing: '', improve: '', expectedRevision: workflowRevision, requestId: crypto.randomUUID() });
        }}>Отказать</Button>}
      </div>}
    </header>
    {(contacts || (hasProfile && resume?.onOpen)) && <section className="ecp-overview" aria-label="Профиль кандидата">
      {contacts && <div className="ecp-meta"><p>{contacts}</p></div>}
      {hasProfile && resume?.onOpen && <Button className="ecp-profile-toggle" variant="ghost" aria-expanded={profileOpen} aria-controls={profileId} onClick={() => setProfileOpen(value => !value)}>Профиль</Button>}
    </section>}
    {(resume || hasProfile) && <div className="ecp-resume">
      <span className="ecp-file-icon"><img src={fileIcon} alt=""/></span>
      <div className="ecp-file-copy"><strong>{resume?.name || (source.cv ? 'Резюме' : 'Профиль')}</strong>{resume?.sizeLabel && <span>{resume.sizeLabel}</span>}</div>
      <div className="ecp-file-actions">
        {resume?.onDownload && <Button variant="ghost" size="icon" aria-label="Скачать резюме" onClick={resume.onDownload}><img src={downloadIcon} alt=""/></Button>}
        {resume?.onOpen ? <Button variant="secondary" onClick={resume.onOpen}>Открыть</Button> : hasProfile && <Button variant="secondary" aria-expanded={profileOpen} aria-controls={profileId} onClick={() => setProfileOpen(value => !value)}>{profileOpen ? 'Свернуть' : 'Открыть'}</Button>}
      </div>
    </div>}
    {profileOpen && <section className="ecp-resume-text" id={profileId} aria-label="Резюме и профиль">
      {!!source.profile?.length && <dl>{source.profile.map((fact, index) => <div key={`${fact.label}:${index}`}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>}
      {source.cv && <p>{source.cv}</p>}
    </section>}
    {meetingUnavailableReason && !onMeet && <p className="ecp-caption">{meetingUnavailableReason}</p>}
    {(error || localError) && <p className="ecp-error" role="alert">{error || localError}</p>}
    {rejection && <form className="ecp-rejection" aria-label="Обратная связь при отказе" onSubmit={async event => {
      event.preventDefault(); if (!onReject || rejecting.current || busy) return;
      rejecting.current = true; setSaving(true); setLocalError('');
      try { if (await onReject(rejection)) finishRejection(); else setLocalError('Не удалось сохранить отказ. Ваши ответы остались в форме.'); }
      catch (cause) { setLocalError(cause instanceof Error ? cause.message : 'Не удалось сохранить отказ.'); }
      finally { rejecting.current = false; setSaving(false); }
    }}>
      {(['reason', 'missing', 'improve'] as const).map((key, index) => <label key={key} htmlFor={`${headingId}-${key}`}>{rejectQuestions[index]}
        <Textarea id={`${headingId}-${key}`} autoFocus={index === 0} required={key === 'reason'} maxLength={4000} value={rejection[key]} disabled={busy || saving} onChange={event => setRejection({ ...rejection, [key]: event.target.value })}/>
      </label>)}
      <div className="ecp-form-actions"><Button type="submit" disabled={busy || saving}>{saving ? 'Сохраняем…' : 'Сохранить отказ'}</Button><Button type="button" variant="ghost" disabled={saving} onClick={finishRejection}>Отмена</Button></div>
    </form>}
    {decisionFeedback && <details className="ecp-saved-rejection"><summary>Обратная связь по отказу</summary>{(['reason', 'missing', 'improve'] as const).map((key, index) => decisionFeedback[key] && <div key={key}><h3>{rejectQuestions[index]}</h3><p>{decisionFeedback[key]}</p></div>)}</details>}
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
