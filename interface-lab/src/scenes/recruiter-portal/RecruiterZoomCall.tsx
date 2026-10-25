import { ZoomMeeting } from '../../components/call/ZoomMeeting';
import { recruiterRequest, type Room } from './api';
import type { RecruiterCallProps } from './RecruiterCall';

/** Recruiter permissions stay in the API; the call window is shared by all roles. */
export function RecruiterZoomCall({ active, token, onClose, onJoined, onZoomHostLeft }: RecruiterCallProps) {
  const identity = active.kind === 'interview'
    ? { candidateId: active.candidateId, interviewId: active.interviewId }
    : { candidateId: active.candidateId, applicationId: active.applicationId, meetingId: active.meetingId };
  const interview = active.candidate?.interview;
  const now = Date.now();
  const unavailable = interview?.status === 'completed' ? 'Встреча завершена'
    : interview?.videoConfigured === false ? 'Видеосвязь недоступна'
    : interview && now < Date.parse(interview.start) - 15 * 60_000 ? 'Вход откроется за 15 минут до встречи'
    : interview && now > Date.parse(interview.end) + 90 * 60_000 ? 'Время подключения истекло' : undefined;
  return <div className="md-zoom-entry-dialog">
    <ZoomMeeting title={`Интервью · ${active.title}`} unavailable={unavailable}
      join={signal => recruiterRequest<Room>(`${active.kind}/join`, token, { ...identity, consent: true }, signal)}
      onJoined={onJoined} onBack={onClose} onLeft={onClose}
      host={{ connected: signal => recruiterRequest(`${active.kind}/connected`, token, identity, signal),
        left: () => { onZoomHostLeft?.(); onClose(); } }}/>
  </div>;
}
