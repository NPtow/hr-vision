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
  async function resumeFile(download: boolean) {
    if (fileBusy) return;
    // Open synchronously so Safari keeps the user's explicit open gesture.
    const target = download ? null : window.open('about:blank', '_blank');
    if (target) target.opener = null;
    setFileBusy(true); setFileError('');
    try {
      const file = await request<{ filename: string; mime: string; contentBase64: string }>('employer/screening/resume', journey.token, {
        candidate: person.id, applicationId: person.applicationId || person.id,
        vacancyId: person.vacancyId || journey.state!.vacancy.id, generation: journey.state!.generation,
      });
      const raw = atob(file.contentBase64);
      const bytes = Uint8Array.from(raw, character => character.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: file.mime }));
      if (target) target.location.replace(url);
      else {
        const link = document.createElement('a'); link.href = url; link.download = file.filename;
        document.body.appendChild(link); link.click(); link.remove();
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (cause) {
      target?.close(); setFileError(cause instanceof Error ? cause.message : 'Не удалось открыть резюме.');
    } finally { setFileBusy(false); }
  }
  return <EmployerCandidateProfile person={person} source={source}
    interview={<EmployerScreening journey={journey} person={person} source={source} appearance="candidate" enableComments/>}
    resume={source.resume ? { name: source.resume.filename,
      sizeLabel: `${Math.max(1, Math.ceil(source.resume.size / 1024))} КБ`,
      onOpen: () => { void resumeFile(false); }, onDownload: () => { void resumeFile(true); },
    } : undefined}
    workflowRevision={person.workflowRevision || 0} busy={journey.busy || fileBusy}
    error={fileError || journey.error}/>
  ;
}
