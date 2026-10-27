import assert from 'node:assert/strict';
import test from 'node:test';
import { blocksAtTime, feedbackBlocks, initialFeedbackPlayback, opinionKey, readOpinion, reduceFeedbackPlayback, visibleFeedbackBlock } from '../src/scenes/assessment-interpretation/explorations/fragment-feedback.ts';
import type { AssessmentCriterion, AssessmentEvidence } from '../src/scenes/assessment-interpretation/assessment-view.ts';

const evidence = (id: string, start: number, end?: number, turnIds?: number[]): AssessmentEvidence => ({ id, seconds: start, endSeconds: end, turnIds, text: 'Fixture', time: '00:10' });
const criterion = (id: string, sources: AssessmentEvidence[]): AssessmentCriterion => ({ id, label: id, state: 'supported', level: null, target: null, summary: '', evidence: sources, nextQuestion: '', basis: 'narrated', stateLabel: '', limitation: '' });
const blocks = feedbackBlocks([
  criterion('A', [evidence('first', 10, 20), evidence('second', 30, 40)]),
  criterion('B', [evidence('shared', 12, 19)]),
], []);
const media = (time: number, playing = true, cause: 'tick' | 'play' | 'pause' | 'seek' | 'end' = 'tick') => ({ type: 'media' as const, time, playing, cause });

test('question follows a semantic block, survives pause, ends exactly at boundary, and stays absent in gaps', () => {
  let state = reduceFeedbackPlayback(initialFeedbackPlayback, media(10, true, 'play'), blocks);
  assert.equal(state.active?.evidenceId, 'first');
  state = reduceFeedbackPlayback(state, media(15), blocks);
  assert.equal(state.active?.evidenceId, 'first');
  state = reduceFeedbackPlayback(state, media(15, false, 'pause'), blocks);
  assert.equal(state.active?.evidenceId, 'first');
  state = reduceFeedbackPlayback(state, media(20, false, 'seek'), blocks);
  assert.equal(state.active, null);
  state = reduceFeedbackPlayback(state, media(25), blocks);
  assert.equal(state.active, null);
});

test('paused seek resolves both forward and backward; metadata alone does not open the question', () => {
  assert.equal(reduceFeedbackPlayback(initialFeedbackPlayback, media(10, false), blocks).active, null);
  let state = reduceFeedbackPlayback(initialFeedbackPlayback, media(35, false, 'seek'), blocks);
  assert.equal(state.active?.evidenceId, 'second');
  state = reduceFeedbackPlayback(state, media(11, false, 'seek'), blocks);
  assert.equal(state.active?.evidenceId, 'first');
  assert.equal(reduceFeedbackPlayback(state, media(11, false, 'end'), blocks).active, null);
});

test('overlapping skills keep the chosen question until it leaves its interval', () => {
  let state = reduceFeedbackPlayback(initialFeedbackPlayback, media(13, true, 'play'), blocks);
  assert.equal(state.active?.criterionId, 'A');
  state = reduceFeedbackPlayback(state, { type: 'prefer', criterionId: 'B' }, blocks);
  state = reduceFeedbackPlayback(state, media(18), blocks);
  assert.equal(state.active?.criterionId, 'B');
  state = reduceFeedbackPlayback(state, media(19), blocks);
  assert.equal(state.active?.criterionId, 'A');
});

test('comment lock retains the exact fragment while video progresses, seeks or ends', () => {
  let state = reduceFeedbackPlayback(initialFeedbackPlayback, media(11), blocks);
  state = reduceFeedbackPlayback(state, { type: 'comment-open' }, blocks);
  state = reduceFeedbackPlayback(state, media(35), blocks);
  assert.equal(state.active?.evidenceId, 'second');
  assert.equal(visibleFeedbackBlock(state, blocks)?.evidenceId, 'first');
  state = reduceFeedbackPlayback(state, media(25, false, 'seek'), blocks);
  assert.equal(visibleFeedbackBlock(state, blocks)?.evidenceId, 'first');
  state = reduceFeedbackPlayback(state, media(40, false, 'end'), blocks);
  assert.equal(visibleFeedbackBlock(state, blocks)?.evidenceId, 'first');
  state = reduceFeedbackPlayback(state, { type: 'comment-close' }, blocks);
  assert.equal(visibleFeedbackBlock(state, blocks), null);
});

test('direct source chooses its skill and fragment without requiring playback', () => {
  const state = reduceFeedbackPlayback(initialFeedbackPlayback, { type: 'source', target: { criterionId: 'B', evidenceId: 'shared' }, time: 12 }, blocks);
  assert.equal(state.playing, false);
  assert.equal(visibleFeedbackBlock(state, blocks)?.criterionId, 'B');
});

test('turn bounds are a fallback, unknown bounds never extend through unrelated speech', () => {
  const result = feedbackBlocks([criterion('C', [evidence('fallback', 4, undefined, [1, 2]), evidence('unknown', 20)])], [
    { id: 1, seconds: 4, endSeconds: 6 }, { id: 2, seconds: 7, endSeconds: 9 }, { id: 3, seconds: 10, endSeconds: 100 },
  ]);
  assert.equal(result[0].end, 9);
  assert.equal(result[1].end, null);
  assert.equal(blocksAtTime(result, 8).length, 1);
  assert.equal(blocksAtTime(result, 9).length, 0);
  assert.equal(blocksAtTime(result, 25).length, 0);
});

test('responses are isolated by candidate, revision, skill and fragment; corrupted storage is tolerated', () => {
  const target = { criterionId: 'A', evidenceId: 'first' };
  const keys = [opinionKey('one', 'v1', target), opinionKey('two', 'v1', target), opinionKey('one', 'v2', target), opinionKey('one', 'v1', { ...target, criterionId: 'B' }), opinionKey('one', 'v1', { ...target, evidenceId: 'second' })];
  assert.equal(new Set(keys).size, 5);
  assert.equal(readOpinion('broken'), null);
  assert.deepEqual(readOpinion('{"answer":"yes","comment":"Уточнить"}'), { answer: 'yes', comment: 'Уточнить' });
  assert.deepEqual(readOpinion('{"answer":"approve","comment":false}'), { answer: undefined, comment: '' });
});
