export type CampaignMode = 'direct' | 'applicants';
export type Campaign = {
  id: string; name: string; accountId: string; mode: CampaignMode; vacancyId: string;
  status: string; template: string; limit: number; paceSeconds: number; createdAt: number; updatedAt: number;
};
export type BrowserState = {
  configured: boolean; loggedIn: boolean; calibrationReady?: boolean; sendingEnabled?: boolean; paused?: boolean; manual?: boolean; accountId?: string; busy?: boolean; captcha?: boolean;
  lastCheckedAt?: string; error?: string;
};
export type CampaignStats = {
  recipients: number; queued: number; sentObserved: number; unknown: number; pageLoaded: number;
  booked: number; completed: number; videoReady: number; transcriptReady: number; blocked?: number;
};
export type Recipient = {
  id: string; jobId?: string; name?: string; candidateId?: string; resumeId?: string; resumeUrl?: string; chatUrl?: string;
  responseId?: string; vacancyId?: string; chatId?: string; state: string; reason?: string; attempts?: number;
  progress?: Partial<Record<keyof CampaignStats, boolean>>;
  importedAt?: string; interviewId?: string;
};
export type CampaignDetail = { campaign: Campaign; recipients: Recipient[]; stats: CampaignStats; sendingEnabled: boolean; account: { blockedReason?: string; nextSendAt?: number } };
export type DiscoveryResult = { complete: boolean; imported: number; skipped?: number; repeated?: number; errors?: (string | { error?: string; message?: string })[]; cursor?: string };
export type DiscoveryStarted = { operationId: string; status: 'queued' | 'running' };
export type DiscoveryState = { operationId?: string; status: 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'interrupted'; result?: DiscoveryResult; error?: string };
const requestErrors: Record<string, string> = { invalid_template: 'В сообщении должна быть ровно одна {booking_url}. Также доступно {name}.', invalid_name: 'Укажите название кампании.', invalid_mode: 'Выберите источник кандидатов.', invalid_limit: 'Укажите лимит от 1 до 1000 кандидатов.', invalid_pace: 'Укажите интервал от 30 до 86400 секунд.', campaign_cancelled: 'Кампания отменена.', campaign_not_found: 'Кампания не найдена.', sending_disabled: 'Отправка ещё не включена.', booking_unavailable: 'Ссылка на запись недоступна. Подготовьте приглашение заново.', account_busy: 'Браузер занят. Дождитесь завершения действия.', job_not_blocked: 'Состояние изменилось. Обновляем данные.', send_may_have_happened: 'Сообщение могло быть отправлено. Повторная отправка запрещена.', invalid_identifier: 'Проверьте номер вакансии и данные кампании.' };
export class OutreachError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
export async function hrRequest<T>(path: string, token?: string, body?: unknown): Promise<T> {
  const response = await fetch('/api/hr/' + path, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(30000),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) throw new OutreachError(requestErrors[data?.error] || data?.error || 'Не удалось связаться с сервисом. Попробуйте ещё раз.', response.status);
  return data as T;
}
export const outreachRequest = <T>(path: string, token: string, body?: unknown) => hrRequest<T>('outreach/' + path, token, body);

export function safeHHLink(value?: string): string | undefined {
  if (!value) return undefined;
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password && (!url.port || url.port === '443') && !url.hash && (url.hostname === 'hh.ru' || url.hostname.endsWith('.hh.ru')) ? url.href : undefined; }
  catch { return undefined; }
}
export function parseVacancy(value: string): string {
  if (/^\d+$/.test(value.trim())) return value.trim();
  const url = safeHHLink(value.trim());
  return url ? new URL(url).pathname.match(/^\/vacancy\/(\d+)\/?$/)?.[1] || '' : '';
}
export function parseResumeURLs(value: string): string[] {
  const entries = value.split(/\s+/).map(item => item.trim()).filter(Boolean);
  const result = [...new Set(entries)];
  if (!result.length || result.some(item => !safeHHLink(item) || !/^\/resume\/[^/]+\/?$/.test(new URL(item).pathname))) {
    throw new Error('Добавьте ссылки на резюме HH: каждая с новой строки.');
  }
  return result;
}
