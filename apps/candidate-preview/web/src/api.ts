import type { Candidate, Directory, DirectoryPage, InterviewCatalogue, Materials, Readiness } from './types';

export const sessionKey = 'hr-vision-connected-session';
const prefix = '/candidate-preview/api';
let memoryToken = '';
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError'; }
}
export const isAuthError = (error: unknown): error is ApiError => error instanceof ApiError && [401, 403].includes(error.status);
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
function token(): string { try { return sessionStorage.getItem(sessionKey) || memoryToken; } catch { return memoryToken; } }
export function clearSession(): void { memoryToken = ''; try { sessionStorage.removeItem(sessionKey); } catch { /* memory-only browser */ } }
export function hasSession(): boolean { return !!token(); }
function invalid(): never { throw new ApiError('Сервис вернул некорректные данные. Повторите загрузку.', 502); }
async function request(path: string, signal?: AbortSignal, session = false): Promise<Record<string, unknown>> {
  const auth = session ? '' : token();
  if (!session && !auth) throw new ApiError('Войдите с доступом менеджера.', 401);
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  if (signal?.aborted) abort(); else signal?.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(() => controller.abort(new DOMException('Время ожидания истекло.', 'TimeoutError')), 30_000);
  try {
    const response = await fetch(session ? '/api/hr/session' : prefix + path, {
      method: session ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer',
      headers: { Accept: 'application/json', ...(auth ? { Authorization: `Bearer ${auth}` } : {}), ...(session ? { 'Content-Type': 'application/json' } : {}) },
      ...(session ? { body: JSON.stringify({ actor: 'manager', scope: 'sfera' }) } : {}), signal: controller.signal,
    });
    if ([401, 403].includes(response.status)) {
      // An obsolete request must not revoke a session issued while it was in flight.
      if (auth && token() === auth && !signal?.aborted) clearSession();
      throw new ApiError('Доступ менеджера истёк или недоступен. Войдите заново через приглашение.', response.status);
    }
    const data: unknown = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(response.status === 409 ? 'Материалы ещё не готовы.' : [404, 410].includes(response.status) ? 'Запрошенные данные недоступны.' : 'Не удалось загрузить данные. Повторите попытку.', response.status);
    if (!object(data) || typeof data.error === 'string') invalid();
    return data;
  } finally { clearTimeout(timeout); signal?.removeEventListener('abort', abort); }
}
export async function signIn(): Promise<void> {
  const data = await request('', undefined, true);
  if (typeof data.token !== 'string' || !data.token.trim() || /\s/.test(data.token)) invalid();
  memoryToken = data.token;
  try { sessionStorage.setItem(sessionKey, data.token); } catch { /* use module memory */ }
}
export async function getDirectory(signal?: AbortSignal): Promise<Directory> {
  const candidates: Candidate[] = [], ids = new Set<string>();
  let offset = 0, count: number | undefined;
  while (true) {
    const value = await request(`/candidates?offset=${offset}&limit=500`, signal);
    const page = value as DirectoryPage;
    if (!Array.isArray(page.candidates) || !Number.isSafeInteger(page.count) || page.count < 0 || page.offset !== offset || page.limit !== 500 || count !== undefined && page.count !== count) invalid();
    count = page.count;
    if (page.candidates.length !== Math.min(500, count - offset)) invalid();
    for (const candidate of page.candidates) {
      if (!object(candidate) || typeof candidate.id !== 'string' || !candidate.id.trim() || ids.has(candidate.id)) invalid();
      ids.add(candidate.id);
    }
    const end = offset + page.candidates.length;
    if (end < count ? !Number.isSafeInteger(page.nextOffset) || page.nextOffset !== end || end <= offset : page.nextOffset !== null) invalid();
    candidates.push(...page.candidates);
    if (page.nextOffset === null) return { candidates, count };
    offset = page.nextOffset;
  }
}
function segment(id: string): string { if (!id.trim()) throw new ApiError('Не указан идентификатор.', 400); return encodeURIComponent(id); }
export async function getReadiness(id: string, signal?: AbortSignal): Promise<Readiness> {
  const data = await request(`/candidates/${segment(id)}/readiness`, signal);
  if (data.candidateId !== id) invalid();
  return data as Readiness;
}
export async function getInterviews(id: string, signal?: AbortSignal): Promise<InterviewCatalogue> {
  const data = await request(`/candidates/${segment(id)}/interviews`, signal);
  if (data.candidateId !== id || !Array.isArray(data.interviews) || data.interviews.some(item => !object(item) || typeof item.id !== 'string' || !Array.isArray(item.parts))) invalid();
  return data as InterviewCatalogue;
}
export async function getMaterials(cid: string, iid: string, signal?: AbortSignal): Promise<Materials> {
  const data = await request(`/candidates/${segment(cid)}/interviews/${segment(iid)}/materials`, signal);
  if (data.candidateId !== cid || data.interviewId !== iid || !Array.isArray(data.parts) || !Array.isArray(data.recordings) || !Array.isArray(data.transcripts)) invalid();
  for (const list of [data.parts, data.recordings, data.transcripts, ...(data.partTranscripts === undefined ? [] : [data.partTranscripts])]) {
    if (!Array.isArray(list) || list.some(item => !object(item) || typeof item.id !== 'string')) invalid();
  }
  return data as Materials;
}
