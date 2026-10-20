import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Dialog } from 'radix-ui';
import { ArrowDownToLine, Check, Copy, ExternalLink, FileText, Mail, MessageCircle, Pencil, X } from 'lucide-react';
import type { RecruiterCandidate } from './api';
import { contactLinks, downloadText, useInterviewCoach, type CoachContext, type InterviewCoachController, type InterviewScript, type ScriptQuestion } from './interview-coach';
import './interview-coach.css';

export function CoachDialog({ open, onOpenChange, title, children, container }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; children: ReactNode; container?: HTMLElement | null;
}) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange}>
    <Dialog.Portal container={container}>
      <Dialog.Overlay className="ric-overlay"/>
      <Dialog.Content className="ric-dialog" aria-describedby={undefined}>
        <header><Dialog.Title>{title}</Dialog.Title><Dialog.Close aria-label="Закрыть" className="ric-icon"><X size={20}/></Dialog.Close></header>
        <div className="ric-dialog-content">{children}</div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

export function ResumeAction({ coach, name, container }: { coach: InterviewCoachController; name: string; container?: HTMLElement | null }) {
  const [open, setOpen] = useState(false);
  const [context, setContext] = useState<CoachContext | null>(null);
  const [error, setError] = useState('');
  async function load() {
    setOpen(true); setError('');
    try { setContext(await coach.context()); } catch (cause) { setError((cause as Error).message); }
  }
  return <>
    <button className="ric-link" onClick={() => void load()}><FileText size={15}/>Резюме</button>
    <CoachDialog open={open} onOpenChange={setOpen} title={name} container={container}>
      {error ? <p role="alert">{error}</p> : !context ? <p>Загружаем резюме…</p> : <>
        <p className="ric-muted">{context.resume?.filename || 'Общие сведения о кандидате'}</p>
        <div className="ric-resume-text">{context.resume?.text || String(context.profile.about || 'Резюме пока не загружено.')}</div>
        {context.resume && <button className="ric-link" onClick={() => downloadText(`${name} — резюме.txt`, context.resume!.text)}><ArrowDownToLine size={16}/>Скачать текст</button>}
      </>}
    </CoachDialog>
  </>;
}

export function InterviewContacts({ candidate, coach }: { candidate: Pick<RecruiterCandidate, 'name' | 'email' | 'telegram' | 'intake'>; coach: InterviewCoachController }) {
  const links = contactLinks(candidate.email || candidate.intake?.contactEmail || '', candidate.telegram || '');
  const [editing, setEditing] = useState(false);
  const [url, setUrl] = useState('');
  const [notice, setNotice] = useState('');
  const backup = coach.data?.backupUrl || '';
  const message = `Здравствуйте${candidate.name ? `, ${candidate.name}` : ''}! Я на связи на нашем интервью. Пока не вижу вас в комнате. Если возникли сложности с подключением, напишите мне${backup ? ` или подключитесь по резервной ссылке: ${backup}` : ', помогу подключиться'}.`;
  async function copy() {
    try { await navigator.clipboard.writeText(message); setNotice('Сообщение скопировано'); }
    catch { setNotice('Не удалось скопировать. Выделите текст сообщения ниже.'); }
  }
  return <section className="ric-contacts" aria-label="Связь с кандидатом">
    <h3>Связь с кандидатом</h3>
    <div className="ric-actions">
      {links.email && <a href={`mailto:${links.email}?subject=${encodeURIComponent('Подключение к интервью')}&body=${encodeURIComponent(message)}`}><Mail size={16}/>{links.email}</a>}
      {links.telegram && <a href={links.telegram} target="_blank" rel="noopener noreferrer"><MessageCircle size={16}/>Telegram<ExternalLink size={12}/></a>}
      {!links.email && !links.telegram && <p className="ric-muted">Почта и Telegram пока не указаны в профиле.</p>}
    </div>
    <div className="ric-backup">
      {backup && <a href={backup} target="_blank" rel="noopener noreferrer" className="ric-link">Резервная встреча<ExternalLink size={14}/></a>}
      {coach.data && coach.data.interviewStatus !== 'completed' && !editing && <button className="ric-link" onClick={() => { setUrl(backup); setEditing(true); }}><Pencil size={14}/>{backup ? 'Изменить ссылку' : 'Добавить резервную ссылку'}</button>}
      {editing && <form onSubmit={async event => {
        event.preventDefault();
        if (await coach.command('backup', { url })) { setEditing(false); setNotice('Резервная ссылка сохранена'); }
      }}>
        <label>Ссылка на Zoom или другую видеовстречу<input type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://…" maxLength={2000}/></label>
        <div className="ric-actions"><button type="submit" className="ric-primary" disabled={coach.busy}>Сохранить</button><button type="button" onClick={() => setEditing(false)}>Отмена</button></div>
      </form>}
    </div>
    <details className="ric-message"><summary>Если кандидат не подключился</summary><p>{message}</p><button className="ric-link" onClick={() => void copy()}><Copy size={15}/>Скопировать сообщение</button></details>
    {notice && <p className="ric-muted" role="status">{notice}</p>}
    {coach.error && <p className="ric-error" role="alert">{coach.error}</p>}
  </section>;
}

export function InterviewPreparation({ candidate, token }: { candidate: RecruiterCandidate; token: string }) {
  const coach = useInterviewCoach({ candidateId: candidate.id, interviewId: candidate.interview!.id, token });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ScriptQuestion[] | null>(null);
  const [editingRevision, setEditingRevision] = useState(0);
  const [localError, setLocalError] = useState('');
  const [review, setReview] = useState<InterviewScript | null>(null);
  const upload = useRef<HTMLInputElement>(null);
  const data = coach.data, latestScript = data?.pinnedRevision ? data.active : data?.latest;
  const script = review || latestScript;
  const canEdit = data?.interviewStatus !== 'completed' && !data?.pinnedRevision;
  useEffect(() => {
    if (open && latestScript && (!review || review.revision === latestScript.revision && review.approvedAt !== latestScript.approvedAt)) setReview(latestScript);
  }, [open, latestScript, review]);
  async function exportContext() {
    setLocalError('');
    try { downloadText(`${candidate.name} — задание агенту.json`, JSON.stringify(await coach.context(), null, 2)); }
    catch (cause) { setLocalError((cause as Error).message); }
  }
  async function importDraft(file?: File) {
    if (!file) return;
    setLocalError('');
    try {
      if (file.size > 256000) throw new Error('Файл слишком большой.');
      const value = JSON.parse(await file.text());
      if (value.candidateId !== candidate.id || value.interviewId !== candidate.interview!.id) throw new Error('Этот скрипт подготовлен для другого кандидата или интервью.');
      if (await coach.command('draft', { expectedRevision: value.expectedRevision, questions: value.questions, source: value.source || '' })) { setReview(null); setOpen(true); }
    } catch (cause) { setLocalError((cause as Error).message); }
  }
  return <section className="ric-preparation" aria-label="Подготовка к интервью">
    <div className="ric-preparation-heading">
      <div><h3>Скрипт интервью</h3><p className="ric-muted">{!data ? 'Загружаем…' : !latestScript ? 'Ещё не подготовлен' : data.pinnedRevision ? `Скрипт интервью · версия ${data.pinnedRevision}` : latestScript.approvedAt ? 'Согласован' : `Черновик ${latestScript.revision} · нужно согласовать`}</p></div>
      <div className="ric-actions"><ResumeAction coach={coach} name={candidate.name}/>{script && <button className="ric-primary" onClick={() => setOpen(true)}>Посмотреть скрипт</button>}</div>
    </div>
    {!script && data && <p className="ric-muted">Подготовьте скрипт с агентом по резюме. Здесь можно прочитать вопросы и согласовать их до звонка.</p>}
    {canEdit && <details className="ric-agent-actions"><summary>Подготовить с агентом</summary>
      <p>Скачайте материалы, подготовьте вопросы в чате с агентом и сохраните его черновик в этом интервью.</p>
      <div className="ric-actions"><button className="ric-link" onClick={() => void exportContext()}><ArrowDownToLine size={15}/>Материалы для агента</button><button className="ric-link" disabled={coach.busy || !data} onClick={() => upload.current?.click()}>Добавить черновик</button></div>
      <input type="file" accept=".json,application/json" ref={upload} hidden onChange={event => { void importDraft(event.target.files?.[0]); event.target.value = ''; }}/>
    </details>}
    {(localError || coach.error) && <p className="ric-error" role="alert">{localError || coach.error}<button className="ric-link" onClick={() => void coach.refresh()}>Обновить</button></p>}
    <InterviewContacts candidate={candidate} coach={coach}/>
    {data && (data.progress.general || Object.values(data.progress.answers || {}).some(answer => answer.note)) && <details className="ric-source">
      <summary>Заметки интервью</summary>
      {data.progress.general && <p>{data.progress.general}</p>}
      {data.active?.questions.filter(q => data.progress.answers?.[q.id]?.note).map(q => <div className="ric-note" key={q.id}><small>{q.topic || q.question}</small><p>{data.progress.answers![q.id].note}</p></div>)}
    </details>}
    <CoachDialog open={open} onOpenChange={value => { setOpen(value); if (!value) { setEditing(null); setReview(null); } }} title="Скрипт интервью">
      <p className="ric-muted">{candidate.name}{script ? ` · версия ${script.revision}` : ''}</p>
      {script && <>
        <ol className="ric-review-questions">{(editing || script.questions).map((q, index) => <li key={q.id}>
          <span>{q.topic}</span>{editing ? <textarea aria-label={`Вопрос ${index + 1}`} value={q.question} maxLength={3000} onChange={event => setEditing(questions => questions!.map((item, i) => i === index ? { ...item, question: event.target.value } : item))}/> : <p>{q.question}</p>}
          {q.why && <small>{q.why}</small>}{q.followups.length > 0 && <details><summary>Уточнения</summary><ul>{q.followups.map((value, i) => <li key={i}>{value}</li>)}</ul></details>}
        </li>)}</ol>
        {script.source && <details className="ric-source"><summary>Основа скрипта</summary><p>{script.source}</p></details>}
        {coach.error && <p className="ric-error" role="alert">{coach.error}</p>}
        {latestScript && script.revision !== latestScript.revision && <p className="ric-error" role="status">Появился новый черновик. <button onClick={() => { setReview(latestScript); setEditing(null); }}>Прочитать новую версию</button></p>}
        {canEdit && <div className="ric-dialog-actions">
          {editing ? <><button className="ric-primary" disabled={coach.busy} onClick={async () => { if (await coach.command('draft', { expectedRevision: editingRevision, questions: editing, source: script.source })) { setEditing(null); setReview(null); } }}>Сохранить черновик</button><button onClick={() => setEditing(null)}>Отмена</button></> : <>
            {!script.approvedAt && <button className="ric-primary" disabled={coach.busy || script.revision !== latestScript?.revision} onClick={() => void coach.command('approve', { revision: script.revision })}><Check size={16}/>Согласовать для интервью</button>}
            <button className="ric-link" disabled={coach.busy} onClick={() => { setEditing(script.questions.map(q => ({ ...q }))); setEditingRevision(script.revision); }}><Pencil size={15}/>Изменить вопросы</button>
          </>}
        </div>}
      </>}
    </CoachDialog>
  </section>;
}
