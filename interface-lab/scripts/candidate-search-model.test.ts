import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { CandidateApplication, CandidateApplicationWorkflow, CandidateMeetingHistory, CandidateOffer } from '../src/scenes/candidate-portal/api.ts';
import { applicationCardTone, applicationMeetingIsJoinable, expandedSearchApplications, orderedSearchApplications, vacancyCardDetails } from '../src/scenes/candidate-portal/candidate-search-model.ts';

const NOW = Date.parse('2026-10-13T12:00:00.000Z');
const MINUTE = 60_000;

function application(id: string, overrides: Partial<CandidateApplication> = {}): CandidateApplication {
  return {
    id, vacancy: { id: `vacancy-${id}`, title: 'Инженер', company: 'Команда', city: 'Москва', remote: true, description: 'Описание работы' },
    status: 'accepted', approvedAutomatically: false, meeting: null, ...overrides,
  };
}

function meeting(overrides: Partial<CandidateMeetingHistory> = {}): CandidateMeetingHistory {
  return { id: 'meeting-1', revision: 1, start: new Date(NOW + 10 * MINUTE).toISOString(), end: new Date(NOW + 40 * MINUTE).toISOString(), status: 'pending', ...overrides };
}

function offer(overrides: Partial<CandidateOffer> = {}): CandidateOffer {
  return {
    id: 'offer-1', version: 1, status: 'sent', role: 'Инженер', compensation: '200 000 ₽', format: 'Удалённо',
    expectations: 'Разработка продукта', startDate: '2026-11-02', createdAt: '2026-10-12T09:00:00Z',
    sentAt: '2026-10-12T10:00:00Z', answeredAt: null, answeredBy: null, ...overrides,
  };
}

function workflow(overrides: Partial<CandidateApplicationWorkflow> = {}): CandidateApplicationWorkflow {
  return {
    applicationId: 'application-1', candidateId: 'candidate-1', vacancyId: 'vacancy-1', interest: 'accepted', revision: 3,
    decision: 'review', afterInterest: 'pending', closedBy: null, meeting: null, offer: null, messages: [], ...overrides,
  };
}

test('archived pending applications remain closed despite active meeting and offer snapshots', () => {
  const archived = application('archived', {
    status: 'pending', archivedAt: '2026-10-13T10:00:00Z',
    workflow: workflow({ meeting: meeting({ status: 'live' }), offer: offer() }),
  });
  assert.equal(applicationCardTone(archived, NOW), 'closed');
  assert.equal(applicationCardTone(application('new', { status: 'pending' }), NOW), 'new');
  assert.deepEqual(orderedSearchApplications([archived, application('new', { status: 'pending' })], NOW).map(item => item.id), ['new', 'archived']);
});

test('closed workflow decisions and answered offers never become actionable', () => {
  for (const overrides of [
    { status: 'declined' as const }, { decision: 'declined' as const }, { afterInterest: 'no' as const },
    { workflow: workflow({ closedBy: 'other-candidate', meeting: meeting({ status: 'live' }) }) },
    { workflow: workflow({ decision: 'declined', offer: offer() }) },
    { offer: offer({ status: 'declined' }) },
  ]) assert.equal(applicationCardTone(application('closed', overrides), NOW), 'closed');
});

test('offers have a separate tone while withdrawn offers and unscheduled applications wait', () => {
  assert.equal(applicationCardTone(application('sent', { workflow: workflow({ offer: offer() }) }), NOW), 'offer');
  assert.equal(applicationCardTone(application('withdrawn', { offer: offer({ status: 'withdrawn' }) }), NOW), 'progress');
  assert.equal(applicationCardTone(application('waiting'), NOW), 'progress');
});

test('pending meetings need confirmation only before the exact start; live meetings need action', () => {
  for (const [offset, expected] of [[1, 'action'], [0, 'progress'], [-1, 'progress']] as const) {
    const item = application('pending', { workflow: workflow({ meeting: meeting({ start: new Date(NOW + offset).toISOString() }) }) });
    assert.equal(applicationCardTone(item, NOW), expected, `${offset}ms before start`);
  }
  assert.equal(applicationCardTone(application('live', { workflow: workflow({ meeting: meeting({ status: 'live' }) }) }), NOW), 'action');
});

test('confirmed and live meetings need action only inside the inclusive server join window', () => {
  const start = NOW + 10 * MINUTE, end = NOW + 40 * MINUTE;
  for (const status of ['confirmed', 'live'] as const) {
    const item = application(status, { workflow: workflow({ meeting: meeting({ status }) }) });
    for (const [time, expected] of [
      [start - 15 * MINUTE - 1, 'progress'], [start - 15 * MINUTE, 'action'],
      [start, 'action'], [end + 90 * MINUTE, 'action'], [end + 90 * MINUTE + 1, 'progress'],
    ] as const) {
      assert.equal(applicationCardTone(item, time), expected, `${status}: ${new Date(time).toISOString()}`);
      assert.equal(applicationMeetingIsJoinable(item, time), expected === 'action');
    }
  }
});

test('invalid or reversed confirmed and live meeting times cannot create an immediate action', () => {
  for (const status of ['confirmed', 'live'] as const) {
    for (const times of [
      { start: 'not-a-date' }, { end: '' }, { start: '', end: 'not-a-date' },
      { start: new Date(NOW + MINUTE).toISOString(), end: new Date(NOW).toISOString() },
    ]) {
      const item = application('invalid', { workflow: workflow({ meeting: meeting({ status, ...times }) }) });
      assert.equal(applicationCardTone(item, NOW), 'progress');
      assert.equal(applicationMeetingIsJoinable(item, NOW), false);
    }
  }
});

test('meeting joins exclude archived, new, closed and unconfirmed applications', () => {
  for (const status of ['confirmed', 'live'] as const) {
    const current = workflow({ meeting: meeting({ status }) });
    for (const item of [
      application('archived', { archivedAt: '2026-10-13T10:00:00Z', workflow: current }),
      application('new', { status: 'pending', workflow: current }),
      application('closed', { workflow: { ...current, closedBy: 'other-candidate' } }),
      application('declined', { status: 'declined', workflow: current }),
    ]) assert.equal(applicationMeetingIsJoinable(item, NOW), false, item.id);
  }
  for (const status of ['pending', 'completed', 'cancelled', 'declined'] as const) {
    assert.equal(applicationMeetingIsJoinable(application(status, { workflow: workflow({ meeting: meeting({ status }) }) }), NOW), false, status);
  }
});

test('stale live meetings fall behind new cards while independent sent offers keep their action', () => {
  const expiredMeeting = meeting({ status: 'live', start: new Date(NOW - 121 * MINUTE).toISOString(), end: new Date(NOW - 91 * MINUTE).toISOString() });
  const stale = application('stale-live', { workflow: workflow({ meeting: expiredMeeting }) });
  const staleWithOffer = application('stale-with-offer', { workflow: workflow({ meeting: expiredMeeting, offer: offer() }) });
  const offerOnly = application('offer-only', { offer: offer() });
  const active = application('active-live', { workflow: workflow({ meeting: meeting({ status: 'live' }) }) });
  const newItem = application('new', { status: 'pending' });
  assert.equal(applicationCardTone(stale, NOW), 'progress');
  assert.equal(applicationCardTone(staleWithOffer, NOW), 'offer');
  assert.equal(applicationMeetingIsJoinable(staleWithOffer, NOW), false);
  assert.deepEqual(orderedSearchApplications([stale, offerOnly, staleWithOffer, newItem, active], NOW).map(item => item.id), [
    'active-live', 'offer-only', 'stale-with-offer', 'new', 'stale-live',
  ]);
});

test('authoritative workflow nulls and exact current meeting prevent stale actions or closure', () => {
  const staleLive = { ...meeting(), status: 'live' as const };
  const stale = application('stale', {
    meeting: staleLive, meetings: [staleLive], offer: offer({ status: 'accepted' }),
    decision: 'declined', afterInterest: 'no', workflow: workflow(),
  });
  assert.equal(applicationCardTone(stale, NOW), 'progress');
  assert.equal(applicationMeetingIsJoinable(stale, NOW), false);
  const exact = application('exact', {
    meeting: staleLive, meetings: [staleLive],
    workflow: workflow({ meeting: meeting({ id: 'replacement', status: 'cancelled' }) }),
  });
  assert.equal(applicationCardTone(exact, NOW), 'progress');
  assert.equal(applicationMeetingIsJoinable(exact, NOW), false);
});

test('completed meetings stay actionable until the candidate answers', () => {
  const completed = meeting({ status: 'completed' });
  assert.equal(applicationCardTone(application('feedback', { workflow: workflow({ meeting: completed }) }), NOW), 'action');
  assert.equal(applicationCardTone(application('continue', { workflow: workflow({ meeting: completed, afterInterest: 'yes' }) }), NOW), 'progress');
  assert.equal(applicationCardTone(application('stop', { workflow: workflow({ meeting: completed, afterInterest: 'no' }) }), NOW), 'closed');
});

test('ordering prioritizes live, other actions, new, waiting and closed without changing objects or stable ties', () => {
  const items = [
    application('waiting-1'),
    application('new-1', { status: 'pending' }),
    application('closed-1', { status: 'pending', archivedAt: '2026-10-12T10:00:00Z' }),
    application('offer', { offer: offer() }),
    application('live-1', { workflow: workflow({ meeting: meeting({ status: 'live' }) }) }),
    application('waiting-2', { workflow: workflow({ meeting: meeting({ status: 'confirmed', start: new Date(NOW + 60 * MINUTE).toISOString(), end: new Date(NOW + 90 * MINUTE).toISOString() }) }) }),
    application('confirm', { workflow: workflow({ meeting: meeting() }) }),
    application('closed-2', { status: 'declined' }),
    application('new-2', { status: 'pending' }),
    application('live-2', { workflow: workflow({ meeting: meeting({ status: 'live' }) }) }),
    application('join', { workflow: workflow({ meeting: meeting({ status: 'confirmed' }) }) }),
  ];
  const before = structuredClone(items);
  const input = Object.freeze(items.map(item => Object.freeze(item)));
  const result = orderedSearchApplications(input, NOW);
  assert.deepEqual(result.map(item => item.id), ['live-1', 'live-2', 'offer', 'confirm', 'join', 'new-1', 'new-2', 'waiting-1', 'waiting-2', 'closed-1', 'closed-2']);
  assert.deepEqual(input, before);
  assert.notEqual(result, input);
  for (const item of result) assert.equal(item, input.find(original => original.id === item.id));
});

test('empty and already ordered inputs return independent arrays', () => {
  const empty = Object.freeze([] as CandidateApplication[]);
  assert.deepEqual(orderedSearchApplications(empty, NOW), []);
  assert.notEqual(orderedSearchApplications(empty, NOW), empty);
  const item = application('single');
  const single = Object.freeze([item]);
  assert.deepEqual(orderedSearchApplications(single, NOW), single);
  assert.notEqual(orderedSearchApplications(single, NOW), single);
});


test('accepted offers keep their colour but archives and explicit closure remain neutral', () => {
  const accepted = application('hired', { workflow: workflow({ offer: offer({ status: 'accepted' }), closedBy: 'candidate-1' }) });
  assert.equal(applicationCardTone(accepted, NOW), 'offer');
  assert.equal(applicationMeetingIsJoinable(accepted, NOW), false);
  assert.equal(applicationCardTone({ ...accepted, workflow: { ...accepted.workflow!, closedBy: 'another-candidate' } }, NOW), 'closed');
  assert.equal(applicationCardTone({ ...accepted, archivedAt: '2026-10-14T10:00:00Z' }, NOW), 'closed');
  assert.equal(applicationCardTone({ ...accepted, status: 'declined' }, NOW), 'closed');
  const waiting = application('waiting');
  assert.deepEqual(orderedSearchApplications([accepted, waiting], NOW).map(item => item.id), ['waiting', 'hired']);
  assert.equal(applicationCardTone(application('new-with-stale-offer', { status: 'pending', offer: offer() }), NOW), 'new');
});

test('wide expansion starts at the selected row while preserving sibling identities', () => {
  const items = Object.freeze(['a', 'b', 'c', 'd', 'e', 'f'].map(id => Object.freeze(application(id))));
  assert.deepEqual(expandedSearchApplications(items, 'b', 3).map(item => item.id), ['b', 'a', 'c', 'd', 'e', 'f']);
  assert.deepEqual(expandedSearchApplications(items, 'e', 3).map(item => item.id), ['a', 'b', 'c', 'e', 'd', 'f']);
  assert.deepEqual(expandedSearchApplications(items, 'd', 2).map(item => item.id), ['a', 'b', 'd', 'c', 'e', 'f']);
  for (const [id, columns] of [['e', 1], ['', 3], ['missing', 3]] as const) {
    const result = expandedSearchApplications(items, id, columns);
    assert.deepEqual(result, items);
    assert.notEqual(result, items);
    result.forEach((item, index) => assert.equal(item, items[index]));
  }
});

test('structured vacancy facts remove only their legacy projection, keeping original prose', () => {
  const vacancy = { ...application('job').vacancy, format: 'Гибрид', salary: '150 000 ₽',
    details: { responsibilities: 'Развивать продукт', requirements: 'Опыт в B2B', employmentType: 'full-time' as const, benefits: 'ДМС' },
    description: 'Небольшая команда.\n\nЗарплата\n150 000 ₽\n\nФормат работы\nГибрид\n\nЗанятость\nПолная занятость\n\nЗадачи\nРазвивать продукт\n\nТребования\nОпыт в B2B\n\nУсловия\nДМС' };
  const result = vacancyCardDetails(vacancy);
  assert.equal(result.description, 'Небольшая команда.');
  assert.deepEqual(result.sections.map(section => section.text), ['Развивать продукт', 'Опыт в B2B', 'ДМС']);
  assert.deepEqual(result.facts, [{ label: 'Формат', value: 'Гибрид · Москва' }, { label: 'Занятость', value: 'Полная занятость' }]);
  const embedded = vacancyCardDetails({ ...vacancy, description: 'Небольшая команда. ДМС и обучение.' });
  assert.equal(embedded.description, 'Небольшая команда. ДМС и обучение.');
  assert.ok(!embedded.sections.some(section => section.text === 'ДМС'));
  assert.equal(vacancyCardDetails({ ...vacancy, details: undefined }).description, vacancy.description);
  assert.deepEqual(vacancyCardDetails({ ...vacancy, description: '', details: {}, city: '', format: '', remote: false }), { description: '', facts: [], sections: [] });
});
