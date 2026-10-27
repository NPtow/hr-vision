import type { CandidateApplication, CandidateVacancy } from './api';
import { applicationAfterInterest, applicationDecision, applicationGroup, applicationOffer, applicationStatus, currentApplicationMeeting, validDate } from './workspace-model.ts';

export type ApplicationCardTone = 'new' | 'action' | 'progress' | 'closed' | 'offer';

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
  const offer = applicationOffer(application);
  if (group === 'history') {
    // Keep the successful result colourful until the process is explicitly archived.
    const accepted = offer?.status === 'accepted' && !application.archivedAt
      && application.status !== 'declined' && applicationDecision(application) !== 'declined'
      && applicationAfterInterest(application) !== 'no'
      && (!application.workflow?.closedBy || application.workflow.closedBy === application.workflow.candidateId);
    return accepted ? 'offer' : 'closed';
  }
  if (group === 'new') return 'new';
  if (offer?.status === 'sent') return 'offer';
  const joinable = applicationMeetingIsJoinable(application, now);
  // An old live snapshot cannot invite a join, but must not hide a separate sent offer.
  if (currentApplicationMeeting(application)?.status === 'live' && !joinable) return 'progress';
  return applicationStatus(application, now).attention || joinable
    ? 'action' : 'progress';
}

/** Keep server order within a priority; live meetings lead the actionable cards. */
export function orderedSearchApplications(applications: readonly CandidateApplication[], now = Date.now()): CandidateApplication[] {
  const priority: Record<ApplicationCardTone, number> = { offer: 1, action: 2, new: 3, progress: 4, closed: 5 };
  return applications.map((application, index) => {
    const tone = applicationCardTone(application, now);
    const live = tone === 'action' && currentApplicationMeeting(application)?.status === 'live'
      && applicationMeetingIsJoinable(application, now);
    // An accepted offer remains colourful, but no longer competes with current actions.
    return { application, index, priority: live ? 0 : applicationGroup(application) === 'history' ? 5 : priority[tone] };
  }).sort((left, right) => left.priority - right.priority || left.index - right.index)
    .map(({ application }) => application);
}

/** Start the expanded card at its row's beginning, without a hole before it. */
export function expandedSearchApplications(applications: readonly CandidateApplication[], selectedId: string, columns: number): CandidateApplication[] {
  const items = [...applications];
  const index = items.findIndex(item => item.id === selectedId);
  if (index < 0) return items;
  const rowStart = Math.floor(index / Math.max(1, Math.floor(columns))) * Math.max(1, Math.floor(columns));
  items.splice(rowStart, 0, ...items.splice(index, 1));
  return items;
}

const employmentLabels = { 'full-time': 'Полная занятость', 'part-time': 'Частичная занятость', project: 'Проектная работа', internship: 'Стажировка', temporary: 'Временная работа' };

/** The API also appends structured facts for legacy clients. Render each fact once. */
export function vacancyCardDetails(vacancy: CandidateVacancy) {
  const details = vacancy.details || {};
  const employment = details.employmentType ? employmentLabels[details.employmentType] : '';
  const appended = [
    ['Зарплата', vacancy.salary], ['Формат работы', vacancy.format], ['Адрес работы', details.address],
    ['Занятость', employment], ['График', details.schedule], ['Опыт', details.experience],
    ['Задачи', details.responsibilities], ['Требования', details.requirements],
    ['Будет плюсом', details.niceToHave], ['Условия', details.benefits],
  ];
  let description = vacancy.description.trim();
  if (vacancy.details) for (const [label, value] of [...appended].reverse()) {
    if (!value) continue;
    const block = `${label}\n${value}`;
    if (description === block) description = '';
    else if (description.endsWith(`\n\n${block}`)) description = description.slice(0, -block.length - 2).trimEnd();
  }
  const facts = [
    { label: 'Формат', value: [vacancy.format || (vacancy.remote ? 'Удалённо' : ''), vacancy.city].filter(Boolean).join(' · ') },
    { label: 'Занятость', value: employment },
    { label: 'График', value: details.schedule },
    { label: 'Опыт', value: details.experience },
    { label: 'Место работы', value: details.address },
  ].filter(fact => fact.value);
  const sections = [
    { title: 'Чем займётесь', text: details.responsibilities },
    { title: 'Что пригодится', text: details.requirements },
    { title: 'Будет плюсом', text: details.niceToHave },
    { title: 'Условия', text: details.benefits },
  ].filter(section => section.text && !description.includes(section.text));
  return { description, facts, sections };
}
