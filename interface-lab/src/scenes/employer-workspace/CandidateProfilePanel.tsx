import { useId, useState } from 'react';
import { Download, FileText } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { request, type Journey, type Person, type SourceProfile } from '../connected-journey/api';
import { EmployerResumePreview } from './EmployerResumePreview';
import type { EmployerCandidateResume } from './employer-candidate-profile-types';
import './employer-candidate-profile.css';

/** Personal source material stays scoped to the application that granted access. */
export function CandidateProfilePanel({ journey, person, source }: { journey: Journey; person: Person; source: SourceProfile }) {
  const [resumeOpen, setResumeOpen] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  const resumeId = useId();
  const facts = source.profile.filter(fact => fact.value?.trim() && !(/^(имя|фио)$/i.test(fact.label.trim()) && fact.value.trim() === person.name));
  const role = source.interviewRole || person.role;
  const showRole = role && !facts.some(fact => fact.value.trim() === role.trim());
  const summary = source.role?.split(' · ').map(value => value.trim())
    .filter(value => value && value !== role?.trim() && !facts.some(fact => fact.value.trim() === value)).join(' · ');
  const contacts = source.contacts?.filter(contact => contact.value.trim()) || [];
  async function loadResume(signal: AbortSignal) {
    const file = await request<{ filename: string; mime: string; contentBase64: string }>('employer/screening/resume', journey.token, {
      candidate: person.id, applicationId: person.applicationId || person.id,
      vacancyId: person.vacancyId || journey.state!.vacancy.id, generation: journey.state!.generation,
    });
    signal.throwIfAborted();
    return new Blob([Uint8Array.from(atob(file.contentBase64), character => character.charCodeAt(0))], { type: file.mime });
  }
  async function downloadResume() {
    if (downloading || !source.resume) return;
    setDownloading(true); setError('');
    try {
      const url = URL.createObjectURL(await loadResume(new AbortController().signal));
      const link = document.createElement('a'); link.href = url; link.download = source.resume.filename;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось загрузить резюме.'); }
    finally { setDownloading(false); }
  }
  const resume: EmployerCandidateResume | undefined = source.resume ? {
    id: `${person.id}:${source.resume.uploadedAt}`, name: source.resume.filename,
    sizeLabel: `${Math.max(1, Math.ceil(source.resume.size / 1024))} КБ`,
    load: loadResume, onDownload: () => { void downloadResume(); },
  } : undefined;
  return <div className="esc-profile-content">
    {summary && <p className="esc-profile-summary">{summary}</p>}
    {(showRole || facts.length > 0 || contacts.length > 0) && <dl className="esc-profile-facts">
      {showRole && <div><dt>Должность</dt><dd>{role}</dd></div>}
      {facts.map((fact, index) => <div key={`${fact.label}:${index}`}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}
      {contacts.map((contact, index) => <div key={`${contact.kind}:${index}`}><dt>{contact.kind === 'email' ? 'Почта' : contact.kind === 'phone' ? 'Телефон' : 'Telegram'}</dt><dd>{contact.value}</dd></div>)}
    </dl>}
    {(resume || source.cv) && <div className="esc-profile-resume">
      <div className="ecp-resume"><span className="ecp-file-icon"><FileText size={20} aria-hidden="true"/></span>
        <div className="ecp-file-copy"><strong>{resume?.name || 'Резюме'}</strong>{resume?.sizeLabel && <span>{resume.sizeLabel}</span>}</div>
        <div className="ecp-file-actions">
          {resume?.onDownload && <Button variant="ghost" size="icon" aria-label="Скачать резюме" disabled={downloading} onClick={resume.onDownload}><Download size={18} aria-hidden="true"/></Button>}
          <Button variant="secondary" aria-expanded={resumeOpen} aria-controls={resumeId} onClick={() => setResumeOpen(value => !value)}>{resumeOpen ? 'Свернуть' : 'Открыть'}</Button>
        </div>
      </div>
      {error && <p className="esc-error" role="alert">{error}</p>}
      {resumeOpen && <section className="ecp-resume-preview" id={resumeId} aria-label="Резюме"><EmployerResumePreview key={resume?.id || 'source-text'} resume={resume} text={source.cv}/></section>}
    </div>}
    {!summary && !showRole && !facts.length && !contacts.length && !resume && !source.cv && <p className="esc-empty">Данные профиля пока не добавлены.</p>}
  </div>;
}
