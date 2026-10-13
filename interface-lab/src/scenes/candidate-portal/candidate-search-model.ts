import type { CandidateApplication } from './api';
import { applicationGroup, applicationOffer, applicationStatus, currentApplicationMeeting, validDate } from './workspace-model.ts';

export type ApplicationCardTone = 'new' | 'action' | 'progress' | 'closed';

export function applicationMeetingIsJoinable(application: CandidateApplication, now = Date.now()): boolean {
  if (applicationGroup(application) !== 'process') return false;
  const meeting = currentApplicationMeeting(application);
  if (!meeting || !['confirmed', 'live'].includes(meeting.status)) return false;
  const start = validDate(meeting.start)?.getTime();
  const end = validDate(meeting.end)?.getTime();
  // Match the server's join window; malformed or stale dates must not create an action.
  return start !== undefined && end !== undefined && end > start
    && start - 15 * 60_000 <= now && now <= end + 90 * 60_000;
}

export function applicationCardTone(application: CandidateApplication, now = Date.now()): ApplicationCardTone {
  const group = applicationGroup(application);
  if (group === 'history') return 'closed';
  if (group === 'new') return 'new';
  const joinable = applicationMeetingIsJoinable(application, now);
  // An old live snapshot cannot invite a join, but must not hide a separate sent offer.
  if (currentApplicationMeeting(application)?.status === 'live' && !joinable && applicationOffer(application)?.status !== 'sent') return 'progress';
  return applicationStatus(application, now).attention || joinable
    ? 'action' : 'progress';
}

/** Keep server order within a priority; live meetings lead the actionable cards. */
export function orderedSearchApplications(applications: readonly CandidateApplication[], now = Date.now()): CandidateApplication[] {
  const priority: Record<ApplicationCardTone, number> = { action: 1, new: 2, progress: 3, closed: 4 };
  return applications.map((application, index) => {
    const tone = applicationCardTone(application, now);
    const live = tone === 'action' && currentApplicationMeeting(application)?.status === 'live'
      && applicationMeetingIsJoinable(application, now);
    return { application, index, priority: live ? 0 : priority[tone] };
  }).sort((left, right) => left.priority - right.priority || left.index - right.index)
    .map(({ application }) => application);
}
