import assert from 'node:assert/strict';
import { test } from 'node:test';
import { bookingAttempt, restoreBookingAttempt } from '../src/scenes/candidate-portal/booking-attempt.ts';
import { candidateAccessMessage, candidateAccessRequestId, type CandidateAccessState } from '../src/scenes/recruiter-portal/candidate-access-model.ts';

const state = (overrides: Partial<CandidateAccessState> = {}): CandidateAccessState => ({
  status: 'not_requested', mode: 'credentials', emailConfigured: true,
  recipientEmail: 'candidate@example.test', canSend: true, blockedReason: null, ...overrides,
});

test('booking retry preserves the same consenting email, slot, and command identity after a lost response', () => {
  const first = bookingAttempt(null, 'private-token', 'slot-a', ' Candidate@Example.Test ', () => 'first');
  assert.deepEqual(first, { token: 'private-token', slotId: 'slot-a', email: 'candidate@example.test', consentProfile: true, requestId: 'first' });
  assert.equal(bookingAttempt(first, 'private-token', 'slot-a', 'CANDIDATE@example.test', () => { throw new Error('must reuse'); }), first);
  assert.equal(bookingAttempt(first, 'private-token', 'slot-b', 'candidate@example.test', () => 'second').requestId, 'second');
  assert.equal(bookingAttempt(first, 'private-token', 'slot-a', 'other@example.test', () => 'third').requestId, 'third');
});

test('booking restore never reuses another invitation or a pre-consent command', () => {
  const command = bookingAttempt(null, 'token-a', 'slot-a', 'candidate@example.test', () => 'first');
  assert.deepEqual(restoreBookingAttempt(JSON.stringify(command), 'token-a'), command);
  assert.equal(restoreBookingAttempt(JSON.stringify(command), 'token-b'), null);
  assert.equal(restoreBookingAttempt(JSON.stringify({ token: 'token-a', slotId: 'slot-a', requestId: 'old' }), 'token-a'), null);
  assert.equal(restoreBookingAttempt('{invalid', 'token-a'), null);
});

test('candidate access command identity survives ambiguous retry and server reload state', () => {
  assert.equal(candidateAccessRequestId(state(), 'in-flight-id', () => { throw new Error('must reuse'); }), 'in-flight-id');
  assert.equal(candidateAccessRequestId(state({ status: 'unknown', requestId: 'server-id' }), 'older', () => 'new'), 'server-id');
  assert.equal(candidateAccessRequestId(state({ status: 'sent', requestId: 'sent-id' }), '', () => 'new'), 'sent-id');
  assert.equal(candidateAccessRequestId(state({ status: 'failed', requestId: 'failed-id' }), 'failed-id', () => 'explicit-retry'), 'explicit-retry');
  assert.equal(candidateAccessRequestId(state({ status: 'failed', requestId: 'failed-id' }), 'new-attempt', () => { throw new Error('must not create again after lost retry'); }, true), 'new-attempt');
});

test('candidate email states distinguish queue, provider acceptance, ambiguity, and unconfigured delivery', () => {
  assert.match(candidateAccessMessage(state({ status: 'queued' })), /ожидает отправки/);
  assert.match(candidateAccessMessage(state({ status: 'sent' })), /передано почтовому сервису/);
  assert.doesNotMatch(candidateAccessMessage(state({ status: 'sent' })), /доставлено/i);
  assert.match(candidateAccessMessage(state({ status: 'unknown' })), /Повторная отправка заблокирована/);
  assert.equal(candidateAccessMessage(state({ emailConfigured: false, canSend: false, blockedReason: 'email_not_configured' })), 'Отправка писем пока недоступна.');
  assert.match(candidateAccessMessage(state({ mode: 'signin' })), /Пароль останется прежним/);
  assert.doesNotMatch(candidateAccessMessage(state({ status: 'failed', canSend: false, blockedReason: 'credentials_changed' })), /повторить/);
  assert.equal(candidateAccessMessage(state({ status: 'failed', canSend: false, blockedReason: 'email_not_configured' })), 'Отправка писем пока недоступна.');
});
