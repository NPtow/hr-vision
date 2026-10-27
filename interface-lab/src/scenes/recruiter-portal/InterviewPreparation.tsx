import { useEffect, useRef, useState } from 'react';
import { Dialog } from 'radix-ui';
import { ArrowDownToLine, ArrowUpRight, Check, Copy, FileText, Mail, MessageCircle, X } from 'lucide-react';
import { recruiterRequest, RecruiterRequestError, type Invitation, type RecruiterCandidate } from './api';
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

export function CopyMeetingLinkButton({
  candidateId,
  token,
  variant = 'link',
  onCopied,
}: {
  candidateId: string;
  token: string;
  variant?: 'link' | 'prejoin';
  onCopied?: (url: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  async function handleCopy() {
    if (busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await recruiterRequest<Invitation>('invitations', token, {
        candidateId,
        scope: 'call',
      });
      const url = result.url;
      try {
        await navigator.clipboard.writeText(url);
      } catch {
        const textarea = document.createElement('textarea');
        textarea.value = url;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        textarea.remove();
      }
      setCopied(true);
      setNotice('Ссылка на встречу скопирована. Отправьте её кандидату в Telegram или мессенджер.');
      onCopied?.(url);
      window.setTimeout(() => setCopied(false), 3000);
      window.setTimeout(() => setNotice(''), 5000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось скопировать ссылку.');
      window.setTimeout(() => setError(''), 4000);
    } finally {
      setBusy(false);
    }
  }

  if (variant === 'prejoin') {
    return (
      <div className="ric-copy-prejoin-wrapper">
        <button
          type="button"
          className={`ric-approved-link-button ${copied ? 'is-copied' : ''}`}
          disabled={busy}
          onClick={() => void handleCopy()}
          aria-label="Скопировать ссылку на встречу для кандидата"
        >
          {copied ? <Check size={16} /> : <Copy size={16} />}
          <span>{copied ? 'Ссылка скопирована' : busy ? 'Копируем…' : 'Скопировать ссылку на встречу'}</span>
        </button>
        {notice && <p className="ric-prejoin-toast" role="status">{notice}</p>}
        {error && <p className="ric-prejoin-toast ric-copy-error" role="alert">{error}</p>}
      </div>
    );
  }

  return (
    <div className="ric-copy-wrapper">
      <button
        type="button"
        className="ric-link"
        disabled={busy}
        onClick={() => void handleCopy()}
        title="Скопировать ссылку на встречу для кандидата"
        aria-label="Скопировать ссылку на встречу для кандидата"
      >
        {copied ? <Check size={15} /> : <Copy size={15} />}
        <span>{copied ? 'Ссылка скопирована' : busy ? 'Копируем…' : 'Скопировать ссылку на встречу'}</span>
      </button>
      {notice && <span className="ric-copy-toast" role="status">{notice}</span>}
      {error && <span className="ric-copy-toast ric-copy-error" role="alert">{error}</span>}
    </div>
  );
}

