import { useState } from 'react';
import { EmployerScreening } from '../connected-journey/EmployerScreening';
import { request, type Journey, type Person, type SourceProfile } from '../connected-journey/api';
import { EmployerCandidateProfile } from './EmployerCandidateProfile';

/** The proposed-candidate view. Progress through stages remains in the task card. */
export function EmployerCandidateWorkspace({ journey, person, source }: {
  journey: Journey; person: Person; source: SourceProfile;
}) {
  const [fileBusy, setFileBusy] = useState(false);
  const [fileError, setFileError] = useState('');
  async function loadResume(signal: AbortSignal) {
    const file = await request<{ filename: string; mime: string; contentBase64: string }>('employer/screening/resume', journey.token, {
      candidate: person.id, applicationId: person.applicationId || person.id,
      vacancyId: person.vacancyId || journey.state!.vacancy.id, generation: journey.state!.generation,
    });
    signal.throwIfAborted();
    const bytes = Uint8Array.from(atob(file.contentBase64), character => character.charCodeAt(0));
    return new Blob([bytes], { type: file.mime });
  }
  async function downloadResume() {
    if (fileBusy) return;
    setFileBusy(true); setFileError('');
    try {
      const url = URL.createObjectURL(await loadResume(new AbortController().signal));
      const link = document.createElement('a'); link.href = url; link.download = source.resume!.filename;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (cause) {
      setFileError(cause instanceof Error ? cause.message : 'Не удалось открыть резюме.');
    } finally { setFileBusy(false); }
  }
  return <EmployerCandidateProfile person={person} source={source}
    interview={<EmployerScreening journey={journey} person={person} source={source} appearance="candidate" enableComments/>}
    resume={source.resume ? { id: `${person.id}:${source.resume.uploadedAt}`, name: source.resume.filename,
      sizeLabel: `${Math.max(1, Math.ceil(source.resume.size / 1024))} КБ`,
      load: loadResume, onDownload: () => { void downloadResume(); },
    } : undefined}
    workflowRevision={person.workflowRevision || 0} busy={journey.busy || fileBusy}
    error={fileError || journey.error}/>
  ;
}
