export type Availability = { start: string; end: string };
export type Vacancy = { id: string; title: string; company: string; city: string; remote: boolean; description: string };
export type Interview = { id: string; status: 'booked' | 'live' | 'completed'; start: string; end: string; recruiterName: string };
export type EmployerMeeting = { id: string; start: string; end: string; status: 'pending' | 'confirmed' | 'live' | 'completed' };
export type Application = { id: string; vacancy: Vacancy; status: 'pending' | 'accepted' | 'declined'; approvedAutomatically: boolean; meeting: EmployerMeeting | null };
export type InternalProfile = { summary: string; evaluation: string; updatedAt?: string };
export type RecruiterCandidate = {
  id: string; email: string; name: string; city: string; desiredRole: string; about: string; telegram: string;
  desiredSalary?: string; workMode?: 'office' | 'hybrid' | 'remote' | 'any';
  geographies: string[]; availability: Availability[]; consentReuse: boolean; autoApprove: boolean;
  interview: Interview | null; applications: Application[]; videoConfigured: boolean; internalProfile?: InternalProfile;
};
export type RecruiterSlot = Availability & { id: string; recruiterName: string; booked: boolean; candidateId?: string };
export type RecruiterState = { candidates: RecruiterCandidate[]; slots: RecruiterSlot[]; vacancies: Vacancy[]; videoConfigured: boolean };
export type Materials = { status: 'processing' | 'ready'; recordings: { id: string; url: string; duration?: number }[]; transcripts?: { id: string; text: string }[] };
export type Room = { url: string; token: string };
export type TranscriptTurn = { id: string; text: string; seconds: number; speaker?: string };

export async function recruiterRequest<T>(path: string, token: string, body?: unknown): Promise<T> {
  const response = await fetch('/api/hr/recruiter/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store', signal: AbortSignal.timeout(30000),
  });
  const data = await response.json().catch(() => ({ error: 'Сервис временно недоступен.' }));
  if (!response.ok) throw new Error(data.error || 'Не удалось выполнить действие.');
  return data as T;
}
export const formatDate = (iso: string) => new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }).format(new Date(iso)) + ' МСК';
export const moscowInput = (date: Date) => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date).replace(' ', 'T');
export const fromMoscowInput = (value: string) => new Date(value + ':00+03:00').toISOString();
export const candidateStatus = (person: RecruiterCandidate) => person.interview?.status === 'completed' ? 'Интервью проведено' : person.interview?.status === 'live' ? 'Идёт интервью' : person.interview ? 'Интервью назначено' : 'Выбирает время интервью';
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
