import { useEffect, useRef, useState } from 'react';
import { Button } from '../../components/ui/button';
import type { EmployerCandidateResume } from './employer-candidate-profile-types';

type DocumentView = { kind: 'pdf' | 'image'; url: string } | { kind: 'text'; text: string } | { kind: 'unsupported' };

/** Render the original bytes. Object URLs live only as long as this disclosure. */
export function EmployerResumePreview({ resume, text }: { resume?: EmployerCandidateResume; text?: string }) {
  const loader = useRef(resume?.load);
  loader.current = resume?.load;
  const [view, setView] = useState<DocumentView | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!loader.current) return;
    const controller = new AbortController();
    let active = true;
    let objectUrl = '';
    setView(null); setError('');
    void loader.current(controller.signal).then(async blob => {
      const signature = new TextDecoder().decode(await blob.slice(0, 5).arrayBuffer());
      let next: DocumentView;
      if (signature === '%PDF-') {
        objectUrl = URL.createObjectURL(blob.type === 'application/pdf' ? blob : new Blob([blob], { type: 'application/pdf' }));
        next = { kind: 'pdf', url: objectUrl };
      } else if (['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(blob.type)) {
        objectUrl = URL.createObjectURL(blob);
        next = { kind: 'image', url: objectUrl };
      } else if (blob.type === 'text/plain' || /\.(txt|md)$/i.test(resume?.name || '')) {
        next = { kind: 'text', text: await blob.text() };
      } else next = { kind: 'unsupported' };
      if (active) setView(next);
      else if (objectUrl) URL.revokeObjectURL(objectUrl);
    }).catch(cause => {
      if (active && !controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Не удалось загрузить резюме.');
    });
    return () => { active = false; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [resume?.id, resume?.name, attempt]);

  if (!resume) return text ? <pre className="ecp-resume-source-text">{text}</pre> : null;
  if (error) return <div className="ecp-resume-status" role="alert"><p>{error}</p><Button variant="ghost" onClick={() => setAttempt(value => value + 1)}>Повторить</Button></div>;
  if (!view) return <p className="ecp-resume-status" role="status">Загрузка…</p>;
  if (view.kind === 'pdf') return <iframe className="ecp-resume-document" title={`Резюме: ${resume.name}`} src={`${view.url}#toolbar=0&navpanes=0&view=FitH`}/>;
  if (view.kind === 'image') return <img className="ecp-resume-image" src={view.url} alt={`Резюме: ${resume.name}`}/>;
  if (view.kind === 'text') return <pre className="ecp-resume-source-text">{view.text}</pre>;
  return <div className="ecp-resume-status"><p>Предпросмотр этого формата недоступен.</p>{resume.onDownload && <Button variant="ghost" onClick={resume.onDownload}>Скачать резюме</Button>}</div>;
}
