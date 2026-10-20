import { useState } from 'react';
import { Dialog } from 'radix-ui';
import { ArrowDownToLine, FileText, Maximize2, Minimize2, X } from 'lucide-react';
import { EmployerResumePreview } from './EmployerResumePreview';
import type { EmployerCandidateResume } from './employer-candidate-profile-types';

/** Keep the original document and the playing interview in the same context. */
export function EmployerResumeDialog({ resume, text, busy, error }: {
  resume?: EmployerCandidateResume; text?: string; busy?: boolean; error?: string;
}) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const changeOpen = (next: boolean) => { setOpen(next); if (!next) setExpanded(false); };
  return <Dialog.Root open={open} onOpenChange={changeOpen}>
    <Dialog.Trigger asChild><button type="button" className="ecp-resume-link" onClick={event => {
      // Native fullscreen only displays descendants of its root. The fallback uses the same root.
      setContainer(event.currentTarget.closest<HTMLElement>('.iv-full'));
    }}><FileText size={18}/><span>Резюме</span></button></Dialog.Trigger>
    <Dialog.Portal container={container}>
      <Dialog.Overlay className="ecp-resume-scrim" onClick={() => changeOpen(false)}/>
      <Dialog.Content className={`ecp-resume-dialog${expanded ? ' ecp-resume-expanded' : ''}`} aria-describedby={undefined}
        onEscapeKeyDown={event => { event.stopPropagation(); changeOpen(false); }}>
        <header className="ecp-resume-toolbar"><Dialog.Title>Резюме</Dialog.Title><div>
          {resume?.onDownload && <button type="button" className="ecp-resume-download" disabled={busy} onClick={resume.onDownload}><ArrowDownToLine size={18}/>Скачать</button>}
          <button type="button" aria-label={expanded ? 'Уменьшить резюме' : 'Развернуть резюме'} onClick={() => setExpanded(value => !value)}>{expanded ? <Minimize2 size={18}/> : <Maximize2 size={18}/>}</button>
          <Dialog.Close asChild><button type="button" aria-label="Закрыть резюме"><X size={21}/></button></Dialog.Close>
        </div></header>
        {error && <p className="ecp-resume-download-error" role="alert">{error}</p>}
        <div className="ecp-resume-scroll"><EmployerResumePreview key={resume?.id || 'text'} resume={resume} text={text}/></div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
