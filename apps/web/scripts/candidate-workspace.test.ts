import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { CandidateApplication, CandidateApplicationWorkflow, CandidateMeetingHistory, CandidateMessage, CandidateOffer } from '../src/scenes/candidate-portal/api.ts';
import {
  applicationAfterInterest, applicationDecision, applicationGroup, applicationHistory,
  applicationOffer, applicationRevision, applicationStatus, canReplyToMeeting,
  currentApplicationMeeting, validDate,
} from '../src/scenes/candidate-portal/workspace-model.ts';

const NOW = Date.parse('2026-10-13T12:00:00.000Z');

function application(overrides: Partial<CandidateApplication> = {}): CandidateApplication {
  return {
    id: 'application-1',
    vacancy: { id: 'vacancy-1', title: 'Инженер', company: 'Команда', city: 'Москва', remote: true, description: 'Описание работы' },
    status: 'accepted', approvedAutomatically: false, meeting: null,
    ...overrides,
  };
}

function meeting(overrides: Partial<CandidateMeetingHistory> = {}): CandidateMeetingHistory {
  return { id: 'meeting-1', revision: 1, start: '2026-10-14T12:00:00.000Z', end: '2026-10-14T13:00:00.000Z', status: 'pending', ...overrides };
}

function offer(overrides: Partial<CandidateOffer> = {}): CandidateOffer {
  return {
    id: 'offer-1', version: 1, status: 'sent', role: 'Инженер', compensation: '200 000 ₽',
    format: 'Удалённо', expectations: 'Разработка продукта', startDate: '2026-11-02',
    createdAt: '2026-10-12T09:00:00.000Z', sentAt: '2026-10-12T10:00:00.000Z', answeredAt: null, answeredBy: null,
    ...overrides,
  };
}

function workflow(overrides: Partial<CandidateApplicationWorkflow> = {}): CandidateApplicationWorkflow {
  return {
    applicationId: 'application-1', candidateId: 'candidate-1', vacancyId: 'vacancy-1',
    interest: 'accepted', revision: 3, decision: 'review', afterInterest: 'pending',
    closedBy: null, meeting: null, offer: null, messages: [], ...overrides,
  };
}

function message(overrides: Partial<CandidateMessage> = {}): CandidateMessage {
  return { id: 'message-1', actor: 'system', actorId: 'system', text: 'Сохранённое событие', at: '2026-10-12T11:00:00.000Z', system: true, ...overrides };
}

test('archive takes precedence over new, active meeting and offer states after search resumes', () => {
  assert.equal(applicationGroup(application({ status: 'pending' })), 'new');
  assert.equal(applicationGroup(application()), 'process');
  for (const status of ['pending', 'accepted', 'declined'] as const) {
    const archived = application({ status, archivedAt: '2026-10-13T10:00:00.000Z', workflow: workflow({ meeting: meeting(), offer: offer() }) });
    assert.equal(applicationGroup(archived), 'history', status);
    assert.equal(applicationStatus(archived, NOW).attention, false, status);
  }
  assert.equal(applicationGroup(application({ archivedAt: null })), 'process');
});

test('candidate refusal, employer closure and final offer replies leave the active process', () => {
  const terminalApplications = [
    application({ status: 'declined' }),
    application({ status: 'pending', decision: 'declined' }),
    application({ afterInterest: 'no' }),
    application({ workflow: workflow({ decision: 'declined', meeting: meeting(), offer: offer() }) }),
    application({ workflow: workflow({ afterInterest: 'no', meeting: meeting() }) }),
    application({ workflow: workflow({ closedBy: 'other-candidate', meeting: meeting() }) }),
    application({ offer: offer({ status: 'accepted' }) }),
    application({ offer: offer({ status: 'declined' }) }),
  ];
  for (const item of terminalApplications) {
    assert.equal(applicationGroup(item), 'history');
    assert.equal(applicationStatus(item, NOW).attention, false);
  }
  for (const status of ['sent', 'superseded', 'withdrawn'] as const) {
    assert.equal(applicationGroup(application({ offer: offer({ status }) })), 'process', status);
  }
});

test('authoritative workflow nulls and zero revision do not revive stale convenience fields', () => {
  const staleMeeting = { ...meeting(), status: 'confirmed' as const };
  const item = application({
    meeting: staleMeeting, meetings: [staleMeeting], offer: offer({ status: 'accepted' }),
    workflowRevision: 99, afterInterest: 'no', decision: 'declined',
    messages: [message({ id: 'stale-message' })],
    workflow: workflow({ revision: 0, meeting: null, offer: null, messages: [] }),
  });
  assert.equal(currentApplicationMeeting(item), null);
  assert.equal(applicationOffer(item), null);
  assert.equal(applicationRevision(item), 0);
  assert.equal(applicationAfterInterest(item), 'pending');
  assert.equal(applicationDecision(item), 'review');
  assert.equal(applicationGroup(item), 'process');
  assert.equal(canReplyToMeeting(item, NOW), false);
  assert.equal(applicationHistory(item).some(event => event.id.startsWith('offer:') || event.id === 'message:stale-message'), false);
});

test('current meeting is resolved by exact ID even when the newer revision was moved earlier', () => {
  const old = { ...meeting({ id: 'old', revision: 1, start: '2026-10-20T12:00:00Z' }), status: 'confirmed' as const };
  const replacement = meeting({ id: 'replacement', revision: 2, start: '2026-10-14T12:00:00Z', status: 'pending' });
  const item = application({
    meeting: old, meetings: [old, replacement],
    workflow: workflow({ meeting: { id: replacement.id, start: replacement.start, end: replacement.end, status: 'cancelled' } }),
  });
  assert.equal(currentApplicationMeeting(item)?.id, 'replacement');
  assert.equal(currentApplicationMeeting(item)?.revision, 2);
  assert.equal(currentApplicationMeeting(item)?.status, 'cancelled');
  assert.equal(canReplyToMeeting(item, NOW), false);
  const unmatched = application({ ...item, workflow: workflow({ meeting: { id: 'third', start: replacement.start, end: replacement.end, status: 'pending' } }) });
  assert.equal(currentApplicationMeeting(unmatched)?.id, 'third');
  assert.equal(currentApplicationMeeting(unmatched)?.revision, undefined);
});

test('legacy payloads retain their exact meeting, revision and offer when workflow is absent', () => {
  const current = { ...meeting({ id: 'current', revision: 4 }), status: 'confirmed' as const };
  const older = meeting({ id: 'older', revision: 3 });
  const terms = offer({ version: 2 });
  const item = application({ meeting: current, meetings: [older], offer: terms, workflowRevision: 8 });
  assert.equal(currentApplicationMeeting(item), current);
  assert.equal(currentApplicationMeeting(application({ meetings: [current, older] })), current);
  assert.equal(applicationOffer(item), terms);
  assert.equal(applicationRevision(item), 8);
});

test('the authoritative offer preserves its version and every term for an explicit reply', () => {
  const current = offer({ version: 7, role: 'Ведущий инженер', compensation: '250 000 ₽', format: 'Гибрид', expectations: 'Вести команду', startDate: '2026-12-01' });
  const item = application({ offer: offer({ version: 6, status: 'accepted' }), workflow: workflow({ revision: 12, offer: current }) });
  assert.deepEqual(applicationOffer(item), current);
  assert.equal(applicationRevision(item), 12);
  assert.equal(applicationGroup(item), 'process');
  assert.equal(applicationStatus(item, NOW).attention, true);
  assert.deepEqual(applicationHistory(item).filter(event => event.id.startsWith('offer:')).map(event => ({ id: event.id, detail: event.detail })), [
    { id: 'offer:offer-1:7', detail: 'Версия 7' },
  ]);
});

test('dated meeting and system events interleave chronologically; unknown snapshots stay undated', () => {
  const first = meeting({ id: 'first', revision: 1, status: 'completed', completedAt: '2026-10-10T11:00:00Z' });
  const second = meeting({ id: 'second', revision: 2, status: 'completed', completedAt: '2026-10-12T11:00:00Z' });
  const pending = meeting({ id: 'future', revision: 3, start: '2026-10-14T12:00:00Z' });
  const item = application({
    meetings: [pending, second, first], archivedAt: '2026-10-13T12:00:00Z',
    workflow: workflow({ meeting: pending, messages: [
      message({ id: 'after-second', at: '2026-10-12T12:00:00Z' }),
      message({ id: 'private-chat', at: '2026-10-11T11:00:00Z', system: false }),
      message({ id: 'after-first', at: '2026-10-10T12:00:00Z' }),
      message({ id: 'invalid-date', at: 'not-a-date' }),
    ] }),
  });
  const events = applicationHistory(item);
  assert.deepEqual(events.filter(event => validDate(event.at)).map(event => event.id), [
    'meeting:first', 'message:after-first', 'meeting:second', 'message:after-second', 'application-1:archived',
  ]);
  assert.equal(events.some(event => event.id === 'message:private-chat'), false);
  assert.equal(events.find(event => event.id === 'meeting:future')?.at, undefined);
  const firstUndated = events.findIndex(event => !validDate(event.at));
  assert.equal(events.slice(firstUndated).every(event => !validDate(event.at)), true);
});

test('meeting snapshots deduplicate by ID and only completed meetings expose their exact material link', () => {
  const completed = { ...meeting({ id: 'completed', revision: 1, completedAt: '2026-10-11T13:00:00Z' }), status: 'completed' as const };
  const cancelled = meeting({ id: 'cancelled', revision: 2, status: 'cancelled', cancellationReason: 'rescheduled', cancelledAt: '2026-10-12T13:00:00Z' });
  const item = application({ meeting: completed, meetings: [cancelled, completed], workflow: workflow({ meeting: cancelled }) });
  const events = applicationHistory(item).filter(event => event.meetingId);
  assert.equal(new Set(events.map(event => event.id)).size, 2);
  assert.deepEqual(events.map(event => ({ meetingId: event.meetingId, materials: event.materials })), [
    { meetingId: 'completed', materials: true }, { meetingId: 'cancelled', materials: false },
  ]);
  assert.deepEqual(applicationHistory(application({ status: 'pending' })), []);
});

test('offer timeline uses the response timestamp, never inventing withdrawal or replacement dates', () => {
  for (const status of ['accepted', 'declined'] as const) {
    const item = application({ offer: offer({ status, answeredAt: '2026-10-13T10:00:00Z' }) });
    assert.equal(applicationHistory(item).find(event => event.id.startsWith('offer:'))?.at, '2026-10-13T10:00:00Z');
  }
  for (const status of ['withdrawn', 'superseded'] as const) {
    const item = application({ offer: offer({ status }) });
    assert.equal(applicationHistory(item).find(event => event.id.startsWith('offer:'))?.at, null, status);
  }
  assert.equal(applicationHistory(application({ offer: offer() })).find(event => event.id.startsWith('offer:'))?.at, '2026-10-12T10:00:00.000Z');
  assert.equal(applicationHistory(application({ offer: offer({ status: 'accepted', answeredAt: null }) })).find(event => event.id.startsWith('offer:'))?.at, null);
});

test('meeting replies stop at the exact start time for both pending and confirmed slots', () => {
  for (const status of ['pending', 'confirmed'] as const) {
    for (const [offset, expected] of [[1, true], [0, false], [-1, false]] as const) {
      const item = application({ workflow: workflow({ meeting: meeting({ status, start: new Date(NOW + offset).toISOString() }) }) });
      assert.equal(canReplyToMeeting(item, NOW), expected, `${status}: ${offset}ms`);
    }
  }
  for (const status of ['live', 'completed', 'cancelled', 'declined'] as const) {
    assert.equal(canReplyToMeeting(application({ workflow: workflow({ meeting: meeting({ status }) }) }), NOW), false, status);
  }
  for (const start of ['not-a-date', '', new Date(NOW).toISOString()]) {
    const item = application({ workflow: workflow({ meeting: meeting({ start }) }) });
    assert.equal(canReplyToMeeting(item, NOW), false, start);
    assert.equal(applicationStatus(item, NOW).attention, false, start);
  }
  assert.equal(canReplyToMeeting(application(), NOW), false);
});
