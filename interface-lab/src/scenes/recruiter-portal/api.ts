import type { CandidateResume, InterviewRecording } from '../candidate-portal/api';

export type Availability = { start: string; end: string };
export type Vacancy = { id: string; title: string; company: string; city: string; remote: boolean; description: string };
export type Interview = { id: string; status: 'booked' | 'live' | 'completed'; start: string; end: string; recruiterName: string; recording?: InterviewRecording };
export type EmployerMeeting = { id: string; start: string; end: string; status: 'pending' | 'confirmed' | 'live' | 'completed' };
export type Application = { id: string; vacancy: Vacancy; status: 'pending' | 'accepted' | 'declined'; approvedAutomatically: boolean; meeting: EmployerMeeting | null };
export type InternalProfile = { summary: string; evaluation: string; updatedAt?: string };
export type RecruiterCandidate = {
  source?: { kind: 'hh' | 'manual' | 'registration'; label: string };
  sourceApplications?: { id: string; title: string; company: string; url: string }[];
  id: string; email: string | null; accountActive?: boolean; intake?: { source: 'manual' | 'hh'; contactEmail: string }; resume?: CandidateResume | null; name: string; city: string; desiredRole: string; about: string; telegram: string;
  desiredSalary?: string; workMode?: 'office' | 'hybrid' | 'remote' | 'any';
  geographies: string[]; availability: Availability[]; consentReuse: boolean; autoApprove: boolean;
  interview: Interview | null; applications: Application[]; videoConfigured: boolean; internalProfile?: InternalProfile;
};
export type RecruiterSlot = Availability & { id: string; recruiterName: string; booked: boolean; candidateId?: string };
export type AvailabilityBlock = { id: string; recruiterName: string; startDate: string; weekdays: number[]; startTime: string; endTime: string; timezone: string };
export type Invitation = { url: string; expiresAt: string | number };
export type RecruiterState = { availabilityBlocks?: AvailabilityBlock[]; candidates: RecruiterCandidate[]; slots: RecruiterSlot[]; vacancies: Vacancy[]; videoConfigured: boolean };
export type Materials = { status: 'processing' | 'ready'; recordings: { id: string; url: string; duration?: number }[]; transcripts?: { id: string; text: string }[] };
export type Room = { url: string; token: string };
export type TranscriptTurn = { id: string; text: string; seconds: number; speaker?: string };

export class RecruiterRequestError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function recruiterRequest<T>(path: string, token: string, body?: unknown): Promise<T> {
  const response = await fetch('/api/hr/recruiter/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store', signal: AbortSignal.timeout(path === 'hh/responses/import' ? 180000 : path === 'interview/start' ? 60000 : 30000),
  });
  const data = await response.json().catch(() => ({ error: 'Сервис временно недоступен.' }));
  if (!response.ok) throw new RecruiterRequestError(data.error || 'Не удалось выполнить действие.', response.status);
  return data as T;
}
export const formatDate = (iso: string) => new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }).format(new Date(iso)) + ' МСК';
export const moscowInput = (date: Date) => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date).replace(' ', 'T');
export const fromMoscowInput = (value: string) => new Date(value + ':00+03:00').toISOString();
export const candidateStatus = (person: RecruiterCandidate) => person.interview?.status === 'completed' ? 'Встреча завершена' : person.interview?.status === 'live' ? 'Идёт интервью' : person.interview ? 'Интервью назначено' : person.accountActive === false ? 'Профиль из CV' : 'Выбирает время интервью';

export type HHVacancy = { id: string; title: string; city: string; localId: string | null; active: boolean; synced: number };
export type HHState = {
  configured: boolean; sendingEnabled: boolean;
  connections: { id: string; employer_id: string; manager_id: string; status: string }[];
  vacancies: HHVacancy[];
  negotiations: { external_id: string; candidate_id: string; cv_status: string; state: string }[];
  rules: { id: string; event_type: string; template: string; enabled: boolean; enabled_since: number }[];
  deliveries: { id: string; candidate_id: string; status: string; attempts: number; last_error: string | null }[];
  skipped: { candidate_id: string; outcome: string }[];
};
export type HHCollection = { id: string; name: string; count: number | null };
export const meetingStatus = { pending: 'Ждём подтверждения кандидата', confirmed: 'Время подтверждено', live: 'Идёт встреча', completed: 'Встреча завершена' };

/** Offer only actual candidate-provided availability, in fixed 30 minute intervals. */
export function availableMeetingStarts(availability: Availability[], now = Date.now()) {
  const result = new Set<string>();
  for (const slot of availability) {
    const start = Date.parse(slot.start), end = Date.parse(slot.end);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) continue;
    for (let time = start; time + 30 * 60_000 <= end && result.size < 240; time += 30 * 60_000) {
      if (time > now) result.add(new Date(time).toISOString());
    }
  }
  return [...result].sort();
}
