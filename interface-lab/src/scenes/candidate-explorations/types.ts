export type Screen = 'search' | 'calendar' | 'profile';
export type SearchFilter = 'new' | 'process' | 'history';
export type Stage = 'new' | 'applied' | 'proposed' | 'confirmed' | 'completed' | 'followup' | 'offer' | 'accepted' | 'declined';
export type JourneyEvent = { id: string; label: string; detail?: string; tone: 'neutral' | 'blue' | 'green'; kind?: 'meeting_completed'; day?: number; time?: string };
export type Vacancy = {
  id: string; company: string; initials: string; role: string; salary: string;
  location: string; format: string; color: string; description: string;
  stage: Stage; meetingDay: number; meetingTime: string; history?: JourneyEvent[]; archived?: boolean;
};
export type Profile = {
  name: string; role: string; city: string; area: string; about: string;
  salaryMin: string; salaryMax: string; employment: string; startBy: string;
  telegram: boolean; autoApprove: boolean; searching: boolean;
};
export type Availability = { day: number; from: string; to: string };
export type VacancyAction = 'accept' | 'decline' | 'confirm' | 'reschedule' | 'join' | 'continue' | 'acceptOffer';
export type SearchProps = {
  variant: number; vacancies: Vacancy[]; selectedId: string;
  filter: SearchFilter; setFilter: (value: SearchFilter) => void;
  select: (id: string) => void; act: (id: string, action: VacancyAction) => void;
  openCalendar: () => void;
};
export type CalendarProps = {
  variant: number; vacancies: Vacancy[]; availability: Availability[];
  setAvailability: (value: Availability[]) => void;
  act: (id: string, action: VacancyAction) => void; openVacancy: (id: string) => void;
};
export type ProfileProps = {
  variant: number; profile: Profile; save: (value: Profile) => void;
};
export type CelebrationProps = { variant: number; runId: number; onResume: () => void; onProcesses: () => void };
export const stageLabels: Record<Stage, string> = {
  new: 'Предложение', applied: 'Вы участвуете', proposed: 'Подтвердите время',
  confirmed: 'Встреча назначена', completed: 'Как прошла встреча?', followup: 'Ждём решения',
  offer: 'Вам предлагают работу', accepted: 'Оффер принят', declined: 'Завершено',
};
export const stageSteps = ['Участие', 'Встреча', 'Решение', 'Оффер'];
export function stageIndex(stage: Stage): number {
  return ({new: 0, applied: 0, proposed: 1, confirmed: 1, completed: 2, followup: 2, offer: 3, accepted: 3, declined: 0})[stage];
}
