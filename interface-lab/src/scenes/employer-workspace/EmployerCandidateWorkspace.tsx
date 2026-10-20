import { useEffect, useRef, useState } from 'react';
import { EmployerScreening } from '../connected-journey/EmployerScreening';
import { initials, request, type Journey, type Person, type SourceProfile } from '../connected-journey/api';
import { EmployerCandidateProfile } from './EmployerCandidateProfile';
import { candidateDecision, respondToCandidate } from './candidate-response';
import type { EmployerCandidateDecision, EmployerCandidateResponseValues } from './employer-candidate-profile-types';

export function sourceFor(person: Person): SourceProfile {
  return person.sourceProfile || { id: person.candidateId || person.id, name: person.name, initials: initials(person.name), role: person.summary || '', interviewRole: person.role || '', interviewDate: '', score: null, duration: null, photo: person.photo || null, video: null, shortChapters: [], fullChapters: [], shortSummary: '', profile: [], cv: '', conclusion: [], questions: [] };
}

/** The proposed-candidate view. Progress through stages remains in the task card. */
export function EmployerCandidateWorkspace({ journey, person, source, onResponseStart, onResponded }: {
  journey: Journey; person: Person; source: SourceProfile;
  onResponseStart?: () => void; onResponded?: () => void;
}) {
  const [fileBusy, setFileBusy] = useState(false);
  const [fileError, setFileError] = useState('');
  const [responding, setResponding] = useState(false);
  const [savedDecision, setSavedDecision] = useState<EmployerCandidateDecision | null>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const identity = { source: 'application' as const, id: person.applicationId || person.id,
    vacancyId: person.vacancyId || journey.state!.vacancy.id!, generation: journey.state!.generation };
  async function respond(values: EmployerCandidateResponseValues) {
    if (pending.current || !mounted.current) return false;
    pending.current = true; setResponding(true);
    onResponseStart?.();
    try {
      const result = await respondToCandidate(journey.token, identity, values);
      if (mounted.current) {
        setSavedDecision(candidateDecision(result.response, result.reviewRejection));
        onResponded?.();
        void journey.refresh();
      }
      return true;
    } finally { pending.current = false; if (mounted.current) setResponding(false); }
  }
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
    interview={header => <EmployerScreening journey={journey} person={person} source={source} appearance="candidate" header={header} enableComments/>}
    resume={source.resume ? { id: `${person.id}:${source.resume.uploadedAt}`, name: source.resume.filename,
      sizeLabel: `${Math.max(1, Math.ceil(source.resume.size / 1024))} КБ`,
      load: loadResume, onDownload: () => { void downloadResume(); },
    } : undefined}
    onRespond={!journey.state!.closedBy && person.reviewState === 'proposed' ? respond : undefined}
    meetingOptions={{ mode: 'available', freeSlots: person.freeSlots }}
    decision={candidateDecision(person.reviewResponse, person.reviewRejection) || savedDecision}
    responseIdentity={`${journey.token}:${identity.source}:${identity.vacancyId}:${identity.id}:${identity.generation}`}
    workflowRevision={person.workflowRevision || 0} busy={journey.busy || fileBusy || responding}
    error={fileError || journey.error}/>
  ;
}
