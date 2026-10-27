export const COMPANY_SITE_URL = 'https://hr-vision-lab.158-160-179-53.sslip.io/demos/company-v1/';
export const EMPLOYER_URL = 'https://hr-vision.158-160-179-53.sslip.io/iframe.html?id=hr-vision-product--manager&viewMode=story';
export const EMPLOYER_ENTRY_KEY = 'hr-vision-employer-entry';
const MAX_BRIEF_LENGTH = 2000;
const ENTRY_LIFETIME = 24 * 60 * 60 * 1000;

export type EmployerEntry = {
  mode: 'login' | 'register';
  createVacancy: boolean;
  description: string;
};
type EntryStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const defaultEntry = (): EmployerEntry => ({ mode: 'register', createVacancy: false, description: '' });
const briefText = (text: string) => text.trim().slice(0, MAX_BRIEF_LENGTH);

export function employerEntryUrl(mode: EmployerEntry['mode'], description?: string): string {
  const url = new URL(EMPLOYER_URL);
  url.searchParams.set('auth', mode);
  if (description !== undefined) {
    url.searchParams.set('intent', 'create-vacancy');
    // A fragment carries the draft across hosts without sending it to their web servers.
    const brief = briefText(description);
    if (brief) url.hash = new URLSearchParams({ brief }).toString();
  }
  return url.href;
}

export function clearEmployerEntry(storage: EntryStorage): void {
  try { storage.removeItem(EMPLOYER_ENTRY_KEY); } catch { /* Storage can be disabled. */ }
}

/** Capture the handoff before authentication, and remove it from the visible URL. */
export function captureEmployerEntry(url: URL, storage: EntryStorage, now = Date.now()): EmployerEntry {
  const mode = url.searchParams.get('auth');
  const createVacancy = url.searchParams.get('intent') === 'create-vacancy';
  if (createVacancy || mode === 'login' || mode === 'register') {
    const entry: EmployerEntry = {
      mode: mode === 'login' ? 'login' : 'register',
      createVacancy,
      description: createVacancy ? briefText(new URLSearchParams(url.hash.slice(1)).get('brief') || '') : '',
    };
    try { storage.setItem(EMPLOYER_ENTRY_KEY, JSON.stringify({ ...entry, expiresAt: now + ENTRY_LIFETIME })); } catch { /* Continue with the in-memory draft. */ }
    url.searchParams.delete('auth');
    url.searchParams.delete('intent');
    if (new URLSearchParams(url.hash.slice(1)).has('brief')) url.hash = '';
    return entry;
  }
  try {
    const saved = JSON.parse(storage.getItem(EMPLOYER_ENTRY_KEY) || 'null');
    if (saved && (saved.mode === 'login' || saved.mode === 'register') && typeof saved.createVacancy === 'boolean'
      && typeof saved.description === 'string' && typeof saved.expiresAt === 'number' && saved.expiresAt > now) {
      return { mode: saved.mode, createVacancy: saved.createVacancy, description: briefText(saved.description) };
    }
  } catch { /* An unavailable or malformed saved draft must not block sign-in. */ }
  clearEmployerEntry(storage);
  return defaultEntry();
}
