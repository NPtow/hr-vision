import assert from 'node:assert/strict';
import test from 'node:test';
import { feedbackBlocks, blocksAtTime, reduceFeedbackPlayback, visibleFeedbackBlock, initialFeedbackPlayback, initialDraft, emptyFeedback, applyFeedbackSuccess, applyRemoteFeedback, feedbackConflict, targetKey, validFeedback } from './fragment-feedback.ts';
import { graphValue } from './graph-contract.ts';

const evidence = (id, seconds, endSeconds, extra = {}) => ({ id, text: 'Source text', time: '00:10', seconds, endSeconds, feedbackQuestion: 'Подтверждает ли фрагмент навык?', ...extra });
const criterion = (id, items, extra = {}) => ({ id, label: id, state: 'unknown', level: null, target: null, summary: 'Source-grounded explanation', evidence: items, ...extra });
const criteria = [criterion('a', [evidence('first', 10, 20), evidence('second', 30, 40)]), criterion('b', [evidence('first', 15, 25)])];
const blocks = feedbackBlocks(criteria);
const media = (state, time, playing = true, cause = 'tick') => reduceFeedbackPlayback(state, { type: 'media', time, playing, cause }, blocks);
const target = { criterionId: 'a', evidenceId: 'first' };

test('only real supplied intervals activate questions; end is exclusive', () => {
  assert.equal(blocksAtTime(blocks, 9.99).length, 0);
  assert.equal(blocksAtTime(blocks, 10).length, 1);
  assert.equal(blocksAtTime(blocks, 16).length, 2);
  assert.deepEqual(blocksAtTime(blocks, 20).map(row => row.criterionId), ['b']);
  assert.equal(blocksAtTime(blocks, 25).length, 0);
  assert.equal(blocksAtTime(blocks, NaN).length, 0);
});

test('unknown end, invalid timing, unavailable media and resume never acquire an invented interval', () => {
  const rows = feedbackBlocks([criterion('a', [evidence('no-end', 10), evidence('bad-end', 10, 9), evidence('nan', NaN, 20), evidence('resume', 10, 20, { sourceKind: 'resume' }), evidence('text', 10, 20, { availability: 'text_only' }), evidence('no-question', 10, 20, { feedbackQuestion: undefined })])]);
  assert.equal(rows.length, 5);
  assert.equal(blocksAtTime(rows, 15).length, 0);
});

test('metadata and an idle clock do not introduce a question; a paused seek does', () => {
  assert.equal(media(initialFeedbackPlayback, 10, false).active, null);
  const pausedSeek = media(initialFeedbackPlayback, 10, false, 'seek');
  assert.deepEqual(pausedSeek.active, target);
  assert.equal(pausedSeek.playing, false);
});

test('source selection shows a question while paused, including an unknown end', () => {
  const items = feedbackBlocks([criterion('a', [evidence('first', 10)])]);
  let state = reduceFeedbackPlayback(initialFeedbackPlayback, { type: 'source', target, time: 10 }, items);
  state = reduceFeedbackPlayback(state, { type: 'media', time: 10, playing: false, cause: 'tick' }, items);
  assert.deepEqual(visibleFeedbackBlock(state, items)?.evidenceId, 'first');
  state = reduceFeedbackPlayback(state, { type: 'media', time: 11, playing: false, cause: 'seek' }, items);
  assert.equal(visibleFeedbackBlock(state, items), null);
});

test('pause retains current question, gaps clear it, and a later fragment is independent', () => {
  let state = media(initialFeedbackPlayback, 12);
  state = media(state, 12, false, 'pause');
  assert.deepEqual(state.active, target);
  assert.equal(state.playing, false);
  state = media(state, 27, false, 'seek');
  assert.equal(state.active, null);
  state = media(state, 31);
  assert.equal(state.active.evidenceId, 'second');
});

test('overlapping criteria respect explicit selection without hiding another target', () => {
  let state = media(initialFeedbackPlayback, 17);
  state = reduceFeedbackPlayback(state, { type: 'prefer', criterionId: 'b' }, blocks);
  assert.deepEqual(state.active, { criterionId: 'b', evidenceId: 'first' });
  assert.equal(blocksAtTime(blocks, 17).length, 2);
  assert.notEqual(targetKey(target), targetKey(state.active));
});

test('comment editing pins its original target through seeks and source clicks', () => {
  let state = media(initialFeedbackPlayback, 12);
  state = reduceFeedbackPlayback(state, { type: 'comment-open' }, blocks);
  state = media(state, 31, false, 'seek');
  assert.equal(visibleFeedbackBlock(state, blocks).evidenceId, 'first');
  state = reduceFeedbackPlayback(state, { type: 'source', target: { criterionId: 'b', evidenceId: 'first' }, time: 17 }, blocks);
  assert.equal(visibleFeedbackBlock(state, blocks).criterionId, 'a');
  state = reduceFeedbackPlayback(state, { type: 'comment-close' }, blocks);
  assert.equal(visibleFeedbackBlock(state, blocks).criterionId, 'b');
});

test('end clears playback while an open comment remains pinned until closed', () => {
  let state = media(initialFeedbackPlayback, 12);
  state = reduceFeedbackPlayback(state, { type: 'comment-open' }, blocks);
  state = media(state, 45, false, 'end');
  assert.equal(state.active, null);
  assert.ok(visibleFeedbackBlock(state, blocks));
  assert.equal(visibleFeedbackBlock(reduceFeedbackPlayback(state, { type: 'comment-close' }, blocks), blocks), null);
});

test('an API success confirms its snapshot without dropping newer typing', () => {
  const base = initialDraft(emptyFeedback(target));
  const submitted = { answer: 'yes', comment: 'Sent' };
  const newer = { ...base, opinion: { answer: 'yes', comment: 'Still typing' }, status: 'saving' };
  const result = applyFeedbackSuccess(newer, submitted, { ...target, ...submitted, version: 1 });
  assert.equal(result.opinion.comment, 'Still typing');
  assert.equal(result.saved.comment, 'Sent');
  assert.equal(result.status, 'idle');
  assert.equal(result.saved.version, 1);
});

test('successful save must advance the version before showing success', () => {
  const base = initialDraft({ ...emptyFeedback(target), version: 2 });
  assert.equal(applyFeedbackSuccess(base, base.opinion, { ...base.saved }).status, 'error');
  assert.equal(applyFeedbackSuccess(base, base.opinion, { ...base.saved, version: 3 }).status, 'saved');
});

test('remote version updates preserve a dirty draft and require explicit conflict resolution', () => {
  const base = initialDraft({ ...emptyFeedback(target), version: 1 });
  const dirty = { ...base, opinion: { answer: 'yes', comment: 'My draft' } };
  const remote = { ...target, answer: 'no', comment: 'Other editor', version: 2 };
  const result = applyRemoteFeedback(dirty, remote);
  assert.equal(result.status, 'conflict');
  assert.equal(result.opinion.comment, 'My draft');
  assert.equal(result.saved.version, 2);
  assert.deepEqual(feedbackConflict({ status: 409, current: remote }, target), remote);
  assert.equal(feedbackConflict({ status: 409, current: { ...remote, evidenceId: 'wrong' } }, target), null);
  assert.equal(validFeedback({ ...remote, version: NaN }, target), false);
});

test('only genuine valid 1–5 values draw a score; absence is not zero', () => {
  assert.equal(graphValue(criterion('a', [])), null);
  assert.equal(graphValue(criterion('a', [], { modelEstimate: { value: 3.8 } })), 3.8);
  assert.equal(graphValue(criterion('a', [], { modelEstimate: { value: 6 } })), null);
  assert.equal(graphValue(criterion('a', [], { state: 'supported', level: Infinity })), null);
});

test('multipart zero-time feedback stays within active recording and never shares speaker clocks', () => {
  const rows = [criterion('a', [evidence('part-a', 0, 5, { recordingId: 'a' }), evidence('part-b', 0, 5, { recordingId: 'b' })])];
  assert.deepEqual(blocksAtTime(feedbackBlocks(rows, 'a'), 0).map(b => b.evidenceId), ['part-a']);
  assert.deepEqual(blocksAtTime(feedbackBlocks(rows, 'b'), 0).map(b => b.evidenceId), ['part-b']);
  assert.deepEqual(feedbackBlocks(rows, 'foreign'), []);
});
