import { useState, type ReactNode } from 'react';
import { Dialog } from 'radix-ui';
import { ArrowDownToLine, ArrowUpRight, FileText, Mail, MessageCircle, X } from 'lucide-react';
import type { RecruiterCandidate } from './api';
import { contactLinks, downloadText, type CoachContext, type InterviewCoachController } from './interview-coach';
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

/** Direct candidate contacts. Opening a composer never sends a message. */
export function InterviewContacts({ candidate }: { candidate: Pick<RecruiterCandidate, 'email' | 'telegram' | 'intake'> }) {
  const links = contactLinks(candidate.email || candidate.intake?.contactEmail || '', candidate.telegram || '');
  return <>
    {links.telegram && <a className="ric-link" href={links.telegram} target="_blank" rel="noopener noreferrer"><MessageCircle size={15}/>@{links.telegram.split('/').pop()}</a>}
    {links.email && <a className="ric-link" href={`mailto:${links.email}`}><Mail size={15}/>{links.email}</a>}
  </>;
}

/** Script preparation and approval happen in the agent chat, outside the cabinet. */
export function InterviewPreparation({ coach, onOpen }: { coach: InterviewCoachController; onOpen: () => void }) {
  return <div className="ric-preparation">
    <span className="ric-script-status" role="status">{coach.error ? 'Статус скрипта недоступен' : !coach.data ? 'Загружаем…' : coach.data.active ? 'Скрипт подготовлен' : 'Скрипт не подготовлен'}</span>
    <button className="ric-primary" onClick={onOpen}>Открыть интервью<ArrowUpRight size={16}/></button>
    {coach.error && <p className="ric-error" role="alert">{coach.error}<button onClick={() => void coach.refresh()}>Повторить</button></p>}
  </div>;
}
