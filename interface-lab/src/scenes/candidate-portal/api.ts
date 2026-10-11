export type Availability = { start: string; end: string };
export type CandidateProfile = {
  name: string;
  city: string;
  desiredRole: string;
  desiredSalary?: string;
  workMode?: 'office' | 'hybrid' | 'remote' | 'any';
  about: string;
  telegram?: string;
  geographies: string[];
  availability: Availability[];
  consentReuse: boolean;
  autoApprove: boolean;
  salaryMin: number | null;
  salaryMax: number | null;
  latestStartDate: string;
  residence: string;
  employmentType: '' | 'full-time' | 'part-time' | 'project' | 'internship';
  resumeReviewed: boolean;
};
export type MissingPreference = 'salaryMin' | 'salaryMax' | 'latestStartDate';
export type CandidatePreferences = Pick<CandidateProfile, MissingPreference | 'consentReuse' | 'autoApprove' | 'about' | 'city' | 'residence' | 'employmentType' | 'resumeReviewed'>;
export type CandidateResume = { filename: string; mime: string; size: number; uploadedAt: string | number; extractedFields: string[] };
export type RecruiterSlot = { id: string; start: string; end: string; recruiterName: string };
export type InterviewRecording = { consentReceived: boolean; consentSource: 'recruiter_oral' | null; startRequested: boolean; recordingRequested: boolean; transcriptionRequested: boolean };
export type CandidateInterview = RecruiterSlot & { status: 'booked' | 'live' | 'completed'; recording?: InterviewRecording };
export type CandidateMeeting = { id: string; start: string; end: string; status: 'pending' | 'confirmed' | 'live' | 'completed' };
export type CandidateApplication = {
  id: string;
  vacancy: { id: string; title: string; company: string; city: string; remote: boolean; description: string };
  status: 'pending' | 'accepted' | 'declined';
  approvedAutomatically: boolean;
  meeting: CandidateMeeting | null;
};
export type DeliveryStatus = { id: string; status: string; event_type?: string; attempts?: number; delivered_at?: number | null; last_error?: string | null };
export type Channels = {
  email?: boolean | { configured: boolean; verified: boolean; status: string; address?: string; lastDelivery?: DeliveryStatus | null };
  telegram?: boolean | { configured: boolean; linked: boolean; canLink: boolean; status: string; lastDelivery?: DeliveryStatus | null };
  emailConfigured?: boolean;
  telegramConfigured?: boolean;
  telegramBindingAvailable?: boolean;
  supportEmail?: string;
  telegramBotUrl?: string;
};
export type CandidateState = CandidateProfile & {
  id: string;
  email: string | null;
  accountActive?: boolean;
  intake?: { source: 'manual' | 'hh'; contactEmail: string };
  interview: CandidateInterview | null;
  applications: CandidateApplication[];
  videoConfigured: boolean;
  channels?: Channels;
  resume: CandidateResume | null;
  onboarding: { step: 'resume' | 'preferences' | 'complete'; missingFields: MissingPreference[] };
};
export type CandidateSession = { authenticated: boolean; candidate?: CandidateState; videoConfigured: boolean; channels?: Channels };
export type GuestState = { name: string; interview: CandidateInterview; videoConfigured: boolean };
export type ClaimState = { candidate: Partial<CandidateProfile> & { name: string; resume: CandidateResume | null; interview?: CandidateInterview | null } | null; claimed: boolean };
export type RoomCredentials = { url: string; token: string };
export type MediaStatus = 'ready' | 'processing' | 'not_recorded' | 'error';
export type MediaChannel = { status: MediaStatus; error?: string };
export type CallMaterials = {
  status: MediaStatus;
  recordings: { id: string; url: string; duration?: number | null; expires?: number | null }[];
  transcripts: { id: string; text: string }[];
  video: MediaChannel;
  transcription: MediaChannel;
  checkedAt: string | number;
};

export class CandidateApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function candidateRequest<T>(path: string, body?: unknown): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), path.endsWith('/materials') ? 60000 : 25000);
  try {
    const response = await fetch(`/api/hr/candidate/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const fallback = response.status === 401 ? 'Войдите снова, чтобы продолжить.' : response.status === 409 ? 'Данные изменились. Обновите страницу и попробуйте ещё раз.' : response.status === 503 ? 'Сервис пока недоступен. Попробуйте позже.' : 'Не удалось сохранить. Попробуйте ещё раз.';
      throw new CandidateApiError(typeof data?.error === 'string' ? data.error : fallback, response.status);
    }
    if (!data || typeof data !== 'object') throw new CandidateApiError('Сервис вернул неверный ответ. Попробуйте ещё раз.', response.status);
    return data as T;
  } catch (error) {
    if (error instanceof CandidateApiError) throw error;
    throw new CandidateApiError(error instanceof Error && error.name === 'AbortError' ? 'Сервер не ответил. Проверьте соединение и попробуйте ещё раз.' : 'Не удалось связаться с сервером. Проверьте соединение.', 0);
  } finally { clearTimeout(timeout); }
}

export const dateLabel = (value: string) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'short' }).format(new Date(value));
export const timeLabel = (value: string) => new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(new Date(value));
export const dateTimeLabel = (value: string) => `${dateLabel(value)}, ${timeLabel(value)}`;
export const timeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
export const localDate = (value = new Date()) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
export const localTime = (value: string) => { const date = new Date(value); return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`; };
