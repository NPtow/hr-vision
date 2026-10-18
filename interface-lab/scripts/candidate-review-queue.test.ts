import assert from 'node:assert/strict';
import test from 'node:test';
import type { EmployerReviewReceipt, Person } from '../src/scenes/connected-journey/api.ts';
import type { IntakePerson } from '../src/scenes/employer-workspace/intake.ts';
import { candidateReviewQueue, currentReviewCandidate, emptyReviewQueueSession, reviewCandidateKey,
  reviewQueueSession, selectedReviewCandidates } from '../src/scenes/employer-workspace/candidate-review-queue.ts';

const vacancyId = 'vacancy-a';
const invite = { decision: 'invite' as const, createdAt: '2026-10-18T23:00:00Z' };
const reject = { decision: 'reject' as const, createdAt: invite.createdAt };
const rejection = { reason: 'Не подходит опыт', missing: '', improve: '' };
const app = (id: string, extra: Partial<Person> = {}): Person => ({
  id, applicationId: id, candidateId: `candidate-${id}`, vacancyId, name: id, reviewState: 'proposed',
  interest: 'accepted', auto: false, freeSlots: [], meeting: null, afterInterest: 'pending',
  decision: 'review', offer: null, messages: [], ...extra,
});
const intake = (id: string, extra: Partial<IntakePerson> = {}): IntakePerson => ({
  id, name: id, role: 'Продажи', contact: '', link: '', note: '', scope: 'sfera', files: [],
  createdAt: '2026-10-18T20:00:00Z', archived: false, ...extra,
});
const receipt = (id: string, extra: Partial<EmployerReviewReceipt> = {}): EmployerReviewReceipt => ({
  ...reject, source: 'application', id, applicationId: id, candidateId: `candidate-${id}`, name: id, vacancyId, ...extra,
});
const keys = (items: ReturnType<typeof candidateReviewQueue>) => items.map(reviewCandidateKey);

test('initial review contains only undecided applications and scoped intake in server order', () => {
  const sources = { vacancyId, people: [app('first'), app('foreign', { vacancyId: 'vacancy-b' }),
    app('selected', { reviewState: 'selected' }), app('invited', { reviewResponse: invite }),
    app('rejected', { reviewResponse: reject }), app('legacy-rejected', { reviewRejection: rejection }),
    app('declined', { decision: 'declined' }), app('second')],
    intake: [intake('imported'), intake('archived', { archived: true }), intake('invited-import', { reviewResponse: invite }),
      intake('rejected-import', { reviewResponse: reject }), intake('legacy-import', { reviewRejection: rejection })] };
  assert.deepEqual(keys(candidateReviewQueue(sources)), ['application:first', 'application:second', 'intake:imported']);
  assert.deepEqual(candidateReviewQueue({ ...sources, vacancyId: '' }), []);
});

test('canonical receipts exclude answered cards even if a stale candidate projection still says proposed', () => {
  const sources = { vacancyId, people: [app('first'), app('second'), app('third')], intake: [],
    receipts: { first: receipt('first'), second: receipt('second', invite), third: receipt('third', { vacancyId: 'vacancy-b' }) } };
  assert.deepEqual(keys(candidateReviewQueue(sources)), ['application:third']);
});

test('an intake promoted to an application occurs once, including when that application is already selected', () => {
  assert.deepEqual(keys(candidateReviewQueue({ vacancyId, people: [app('first', { candidateId: 'imported', reviewState: 'selected' })],
    intake: [intake('imported'), intake('next')] })), ['intake:next']);
});

test('an eligible deep link opens that exact source; an answered or foreign link continues the current vacancy queue', () => {
  const queue = candidateReviewQueue({ vacancyId, people: [app('first'), app('answered', { reviewResponse: reject })], intake: [intake('imported')] });
  assert.equal(currentReviewCandidate(queue, { source: 'intake', id: 'imported' })?.id, 'imported');
  assert.equal(currentReviewCandidate(queue, { source: 'application', id: 'answered' })?.id, 'first');
  assert.equal(currentReviewCandidate(queue, { source: 'application', id: 'foreign' })?.id, 'first');
  assert.equal(currentReviewCandidate(queue, { source: 'application', id: 'imported' })?.id, 'first');
});

test('pending and lost acknowledgements retain the exact form snapshot despite server polling', () => {
  const first = app('first', { workflowRevision: 7 });
  const queue = candidateReviewQueue({ vacancyId, people: [first, app('second')], intake: [] });
  const active = queue[0];
  const session = reviewQueueSession(emptyReviewQueueSession, { type: 'submitting', candidate: active });
  // The server committed while the HTTP acknowledgement was lost; polling removes the first.
  const afterCommit = candidateReviewQueue({ vacancyId, people: [app('first', { reviewState: 'selected', reviewResponse: invite }), app('second')], intake: [] });
  assert.equal(currentReviewCandidate(afterCommit, null, session), active);
  assert.equal(currentReviewCandidate(afterCommit, null, session)?.person, first);
  assert.equal((currentReviewCandidate(afterCommit, null, session)?.person as Person).workflowRevision, 7);
  assert.deepEqual(session.acknowledged, []);
  const confirmed = reviewQueueSession(session, { type: 'confirmed', candidate: active });
  assert.equal(currentReviewCandidate(afterCommit, null, confirmed)?.id, 'second');
});

test('each successful server decision advances once even before list refresh, ending in an empty queue', () => {
  const queue = candidateReviewQueue({ vacancyId, people: [app('first')], intake: [intake('imported')] });
  let session = emptyReviewQueueSession;
  for (const active of queue) {
    assert.equal(currentReviewCandidate(queue, null, session), active);
    session = reviewQueueSession(session, { type: 'submitting', candidate: active });
    session = reviewQueueSession(session, { type: 'confirmed', candidate: active });
    const replay = reviewQueueSession(session, { type: 'confirmed', candidate: active });
    assert.equal(replay, session);
  }
  assert.equal(currentReviewCandidate(queue, null, session), undefined);
  assert.deepEqual(session.acknowledged, ['application:first', 'intake:imported']);
});

test('a new vacancy/session does not inherit a held candidate or acknowledgements from the old one', () => {
  const first = candidateReviewQueue({ vacancyId, people: [app('first')], intake: [] })[0];
  const oldSession = reviewQueueSession(emptyReviewQueueSession, { type: 'submitting', candidate: first });
  const otherQueue = candidateReviewQueue({ vacancyId: 'vacancy-b', people: [app('second', { vacancyId: 'vacancy-b' })], intake: [] });
  assert.equal(oldSession.held?.id, 'first');
  assert.equal(currentReviewCandidate(otherQueue, { source: 'application', id: 'first' }, emptyReviewQueueSession)?.id, 'second');
});

test('selected candidates include real funnel applications and private invited profiles without rejected or foreign entries', () => {
  const privateDraft = intake('private-invite', { reviewResponse: { ...invite, meetingStatus: 'draft', meetingStart: '2026-10-20T10:00:00Z' } });
  const selected = selectedReviewCandidates({ vacancyId, people: [app('unreviewed'),
    app('in-funnel', { reviewState: 'selected' }), app('pending-invite', { reviewResponse: invite }),
    app('rejected', { reviewState: 'selected', decision: 'declined' }), app('foreign', { vacancyId: 'vacancy-b', reviewState: 'selected' })],
    intake: [intake('not-reviewed'), privateDraft, intake('rejected-import', { archived: true, reviewResponse: reject })] });
  assert.deepEqual(keys(selected), ['application:in-funnel', 'application:pending-invite', 'intake:private-invite']);
  assert.equal(selected[2].person, privateDraft);
  assert.equal('meeting' in selected[2].person, false);
});
