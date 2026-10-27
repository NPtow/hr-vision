import type { CandidateStage } from '../employer-workspace/candidate-stage-types';

export type MeetingState = 'pending' | 'confirmed' | 'live' | 'completed' | 'cancelled';
export type Scenario = 'pending' | 'confirmed' | 'live' | 'completed' | 'repeat';
export type DemoMeeting = {
  id: string; title: string; start: string; status: MeetingState;
  organizer: string; participants: string[]; agenda: string; feedback: string;
  feedbackAuthor: string; materialsReady: boolean; changes: string[];
};
export const staff = [
  { id: 'nikita', name: 'Никита', role: 'Организатор', initials: 'Н', color: '#e7e3f8' },
  { id: 'alexey', name: 'Алексей Орлов', role: 'Руководитель продаж', initials: 'АО', color: '#e6edf8' },
  { id: 'maria', name: 'Мария Соколова', role: 'Рекрутёр', initials: 'МС', color: '#efe6e2' },
];
export const interview: CandidateStage = {
  id: 'screening-dina', kind: 'interview', title: 'Интервью', status: 'completed',
  date: '2026-10-16T10:00:00+03:00', notes: '', documents: [],
};
export function makeMeeting(id: string, state: MeetingState, previous = false): DemoMeeting {
  return {
    id, title: previous ? 'Знакомство с командой' : 'Встреча с руководителем',
    start: previous ? '2026-10-18T11:00:00+03:00' : '2026-10-22T14:30:00+03:00',
    status: state, organizer: 'nikita', participants: ['alexey', 'maria'],
    agenda: previous ? 'Опыт продаж и задачи команды.' : 'Разберём сделку и обсудим первые три месяца в команде.',
    feedback: state === 'completed' ? 'Уверенно ведёт клиента от первого контакта до сделки. На следующей встрече разберём длинный цикл продажи и план первых трёх месяцев.' : '',
    feedbackAuthor: 'Алексей Орлов', materialsReady: state === 'completed', changes: [],
  };
}
export function seedScenario(scenario: Scenario): DemoMeeting[] {
  return scenario === 'repeat'
    ? [makeMeeting('meeting-1', 'completed', true), makeMeeting('meeting-2', 'confirmed')]
    : [makeMeeting('meeting-1', scenario)];
}
export function asStage(meeting: DemoMeeting): CandidateStage {
  return { id: meeting.id, kind: 'meeting', title: meeting.title, status: meeting.status,
    date: meeting.start, notes: '', documents: [] };
}
export function dateLabel(start: string) {
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', timeZone: 'Europe/Moscow' }).format(new Date(start));
}
export function timeLabel(start: string) {
  return new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(start));
}
export function endTime(start: string) {
  return timeLabel(new Date(new Date(start).getTime() + 45 * 60_000).toISOString());
}
