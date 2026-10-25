import { EmployerResumeDialog } from '../employer-workspace/EmployerResumeDialog';
import { candidateRequest, type CandidateState } from './api';
import '../employer-workspace/employer-candidate-profile.css';

export function OnboardingResume({ candidate, onReplace }: { candidate: CandidateState; onReplace: () => void }) {
  const resume = candidate.resume;
  return <section className="cp-onboarding-resume" aria-label="Ваше резюме">
    {resume ? <EmployerResumeDialog resume={{ id: candidate.id + String(resume.uploadedAt), name: resume.filename, load: async () => {
      const data = await candidateRequest<{mime: string; contentBase64: string}>('resume');
      return new Blob([Uint8Array.from(atob(data.contentBase64), character => character.charCodeAt(0))], {type: data.mime});
    } }}/> : <span>Резюме</span>}
    <button type="button" onClick={onReplace}>{resume ? 'Заменить' : 'Добавить'}</button>
  </section>;
}
