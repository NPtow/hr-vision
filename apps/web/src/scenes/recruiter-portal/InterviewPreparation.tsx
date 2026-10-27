import { useEffect, useRef, useState } from 'react';
import { Dialog } from 'radix-ui';
import { ArrowDownToLine, ArrowUpRight, FileText, Mail, MessageCircle, X } from 'lucide-react';
import { RecruiterRequestError, type RecruiterCandidate } from './api';
import { EmployerResumePreview } from '../employer-workspace/EmployerResumePreview';
import { contactLinks, type InterviewCoachController } from './interview-coach';
import '../employer-workspace/employer-candidate-profile.css';
import './interview-coach.css';

export function ResumeAction({ coach, name, container }: { coach: InterviewCoachController; name: string; container?: HTMLElement | null }) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<{ name: string; blob: Blob } | null>(null);
  const [fallback, setFallback] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  useEffect(() => () => { sequence.current++; }, []);
  async function load() {
    const current = ++sequence.current;
    setLoading(true); setError(''); setFile(null); setFallback('');
    try {
      // The original-file endpoint accepts PDF/images. Other stored formats
      // retain their readable source text through the same authorized context.
      const original = await coach.originalResume().catch(cause => {
        if (cause instanceof RecruiterRequestError && cause.status === 409) return null;
        throw cause;
      });
      const resume = original?.resume;
      if (current !== sequence.current) return;
      if (resume) {
        const bytes = Uint8Array.from(atob(resume.contentBase64), character => character.charCodeAt(0));
        setFile({ name: resume.filename, blob: new Blob([bytes], { type: resume.mimeType }) });
      } else {
        const context = await coach.context();
        if (current === sequence.current) setFallback(context.resume?.text || String(context.profile.about || 'Резюме пока не загружено.'));
      }
    } catch (cause) {
      if (current === sequence.current) setError(cause instanceof Error ? cause.message : 'Не удалось открыть резюме.');
    } finally { if (current === sequence.current) setLoading(false); }
  }
  function changeOpen(next: boolean) {
    setOpen(next);
    if (next) void load();
    else { sequence.current++; setFile(null); setFallback(''); }
  }
  function download() {
    if (!file) return;
    const url = URL.createObjectURL(file.blob);
    const link = document.createElement('a'); link.href = url; link.download = file.name;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  return <Dialog.Root open={open} onOpenChange={changeOpen}>
    <Dialog.Trigger asChild><button className="ric-link"><FileText size={15}/>Резюме</button></Dialog.Trigger>
    <Dialog.Portal container={container}>
      <Dialog.Overlay className="ric-overlay"/>
      <Dialog.Content className="ric-dialog ric-resume-dialog" aria-describedby={undefined}>
        <header>
          <div className="ric-resume-heading"><Dialog.Title>{name}</Dialog.Title><p>{file?.name || 'Резюме'}</p></div>
          <div className="ric-resume-actions">
            {file && <button className="ric-icon" aria-label="Скачать резюме" title="Скачать резюме" onClick={download}><ArrowDownToLine size={19}/></button>}
            <Dialog.Close aria-label="Закрыть резюме" className="ric-icon"><X size={21}/></Dialog.Close>
          </div>
        </header>
        <div className="ric-resume-content">
          {error ? <div className="ecp-resume-status" role="alert"><p>{error}</p><button className="ric-link" onClick={() => void load()}>Повторить</button></div> : loading ? <p className="ecp-resume-status" role="status">Открываем резюме…</p> : <EmployerResumePreview resume={file ? { id: file.name, name: file.name, load: async () => file.blob, onDownload: download } : undefined} text={fallback}/>}
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
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
