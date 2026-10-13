import assert from 'node:assert/strict';
import { test } from 'node:test';
import { captureEmployerEntry, clearEmployerEntry, employerEntryUrl, EMPLOYER_ENTRY_KEY, EMPLOYER_URL } from '../src/lib/employer-entry.ts';

function storage() {
  const entries = new Map<string, string>();
  return { getItem: (key: string) => entries.get(key) ?? null, setItem: (key: string, value: string) => { entries.set(key, value); }, removeItem: (key: string) => { entries.delete(key); } };
}

test('site handoff carries only an explicit brief, outside the server query', () => {
  const brief = 'Нужен менеджер B2B: Москва, 150–180 тыс. ₽ & CRM';
  const url = new URL(employerEntryUrl('register', brief));
  assert.equal(url.origin, new URL(EMPLOYER_URL).origin);
  assert.equal(url.searchParams.get('intent'), 'create-vacancy');
  assert.equal(url.searchParams.has('brief'), false);
  assert.equal(new URLSearchParams(url.hash.slice(1)).get('brief'), brief);
  const saved = storage();
  const entry = captureEmployerEntry(url, saved, 100);
  assert.deepEqual(entry, { mode: 'register', createVacancy: true, description: brief });
  assert.equal(url.href, EMPLOYER_URL);
  // Registration and reload must not consume the intent before the form is reached.
  assert.deepEqual(captureEmployerEntry(new URL(url), saved, 200), entry);
  clearEmployerEntry(saved);
  assert.equal(captureEmployerEntry(new URL(url), saved, 300).createVacancy, false);
});

test('a new login link clears a previous unfinished vacancy handoff', () => {
  const saved = storage();
  captureEmployerEntry(new URL(employerEntryUrl('register', 'Old draft')), saved, 100);
  const url = new URL(employerEntryUrl('login'));
  assert.deepEqual(captureEmployerEntry(url, saved, 200), { mode: 'login', createVacancy: false, description: '' });
});

test('start without an edited brief opens an empty vacancy, not sample site data', () => {
  const url = new URL(employerEntryUrl('register', ''));
  assert.equal(url.hash, '');
  assert.deepEqual(captureEmployerEntry(url, storage()), { mode: 'register', createVacancy: true, description: '' });
  assert.equal(new URL(employerEntryUrl('login')).searchParams.has('intent'), false);
});

test('expired, malformed and unavailable storage cannot break entry', () => {
  const saved = storage();
  captureEmployerEntry(new URL(employerEntryUrl('register', 'Expired')), saved, 100);
  assert.equal(captureEmployerEntry(new URL(EMPLOYER_URL), saved, 100 + 86400000).createVacancy, false);
  saved.setItem(EMPLOYER_ENTRY_KEY, 'invalid JSON');
  assert.equal(captureEmployerEntry(new URL(EMPLOYER_URL), saved).description, '');
  const unavailable = { getItem() { throw new Error(); }, setItem() { throw new Error(); }, removeItem() { throw new Error(); } };
  assert.equal(captureEmployerEntry(new URL(employerEntryUrl('register', 'Draft')), unavailable).description, 'Draft');
  assert.equal(captureEmployerEntry(new URL(EMPLOYER_URL), unavailable).createVacancy, false);
});

test('briefs are bounded and external redirect parameters are not followed', () => {
  const url = new URL(employerEntryUrl('register', 'a'.repeat(5000)));
  url.searchParams.set('returnTo', 'https://example.com');
  const entry = captureEmployerEntry(url, storage());
  assert.equal(entry.description.length, 2000);
  assert.deepEqual(Object.keys(entry).sort(), ['createVacancy', 'description', 'mode']);
});
