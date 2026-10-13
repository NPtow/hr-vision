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
export type CandidateMeeting = { id: string; start: string; end: string; status: 'pending' | 'confirmed' | 'live' | 'completed'; revision?: number; completedAt?: string | null };
export type CandidateMeetingHistory = Omit<CandidateMeeting, 'status'> & {
  status: CandidateMeeting['status'] | 'cancelled' | 'declined';
  cancelledAt?: string | null;
  cancellationReason?: 'manager' | 'candidate' | 'rescheduled' | 'search_stopped' | null;
};
export type CandidateOffer = {
  id: string; version: number; status: 'sent' | 'accepted' | 'declined' | 'superseded' | 'withdrawn';
  role: string; compensation: string; format: string; expectations: string; startDate: string;
  createdAt: string; sentAt: string | null; answeredAt: string | null; answeredBy: string | null;
};
export type CandidateMessage = { id: string; actor: string; actorId: string; text: string; at: string; system: boolean };
export type CandidateApplicationWorkflow = {
  applicationId: string; candidateId: string; vacancyId: string;
  interest: 'pending' | 'accepted' | 'declined'; revision: number;
  decision: 'review' | 'pool' | 'declined'; afterInterest: 'pending' | 'yes' | 'no';
  closedBy: string | null;
  meeting: Pick<CandidateMeetingHistory, 'id' | 'status' | 'start' | 'end' | 'completedAt'> | null;
  offer: CandidateOffer | null;
  messages: CandidateMessage[];
};
export type CandidateWorkflowCommand = { applicationId: string; requestId?: string; expectedRevision?: number } & (
  { action: 'after' | 'after.interest'; meetingId: string; value: 'yes' | 'no' }
  | { action: 'offer.reply'; version: number; value: 'accepted' | 'declined' }
  | { action: 'chat'; text: string }
);
export type CandidateMeetingReference = { applicationId: string; meetingId: string; revision?: number };
export type CandidateVacancy = {
  id: string; title: string; company: string; city: string; remote: boolean; description: string; salary?: string;
  format?: string;
  details?: {
    address?: string; schedule?: string; responsibilities?: string; requirements?: string;
    niceToHave?: string; benefits?: string; experience?: string;
    employmentType?: '' | 'full-time' | 'part-time' | 'project' | 'internship' | 'temporary';
    salaryMin?: number | null; salaryMax?: number | null; currency?: string;
    salaryPeriod?: '' | 'month' | 'year' | 'hour' | 'shift'; salaryTax?: '' | 'gross' | 'net';
  };
};
export type CandidateApplication = {
  id: string;
  archivedAt?: string | null;
  vacancy: CandidateVacancy;
  status: 'pending' | 'accepted' | 'declined';
  approvedAutomatically: boolean;
  meeting: CandidateMeeting | null;
  meetings?: CandidateMeetingHistory[];
  workflow?: CandidateApplicationWorkflow;
  offer?: CandidateOffer | null;
  decision?: CandidateApplicationWorkflow['decision'];
  afterInterest?: CandidateApplicationWorkflow['afterInterest'];
  messages?: CandidateMessage[];
  workflowRevision?: number;
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
  searching?: boolean;
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

export const candidateWorkflowRequest = (body: CandidateWorkflowCommand) => candidateRequest<CandidateState>('workflow', body);
export const candidateMeetingReply = (action: 'confirm' | 'decline', body: CandidateMeetingReference) =>
  candidateRequest<CandidateState>(`meeting/${action}`, body);

export const dateLabel = (value: string) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'short' }).format(new Date(value));
export const timeLabel = (value: string) => new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(new Date(value));
export const dateTimeLabel = (value: string) => `${dateLabel(value)}, ${timeLabel(value)}`;
export const timeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
export const localDate = (value = new Date()) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
export const localTime = (value: string) => { const date = new Date(value); return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`; };
