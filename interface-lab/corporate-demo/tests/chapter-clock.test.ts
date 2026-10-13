import test from 'node:test';
import assert from 'node:assert/strict';
import { ChapterClock } from '../src/chapter-clock.ts';

test('manual feedback playback ignores delayed scroll events', () => {
  const clock = new ChapterClock();
  clock.scroll(.1);
  clock.seek(.696);
  clock.play();
  clock.advance(32, 18000);
  const current = clock.position;
  assert.equal(clock.scroll(.1), current);
  assert.equal(clock.playing, true);
});
test('user interruption gives scroll ownership back in both directions', () => {
  const clock = new ChapterClock();
  clock.seek(.23);
  clock.play();
  clock.advance(64, 10000);
  clock.takeScrollControl();
  assert.equal(clock.scroll(.5), .5);
  assert.equal(clock.scroll(.12), .12);
  assert.equal(clock.advance(64, 10000), .12);
});
test('retry cancels a running animation before accepting a different answer', () => {
  const clock = new ChapterClock();
  clock.seek(.696);
  clock.play();
  clock.advance(64, 18000);
  clock.seek(.648);
  assert.equal(clock.playing, false);
  assert.equal(clock.advance(64, 18000), .648);
  clock.play();
  assert.ok(clock.advance(64, 18000) > .648);
});
test('scroll anchoring cannot close a profile after a manual seek', () => {
  const clock = new ChapterClock();
  clock.seek(1);
  assert.equal(clock.scroll(.93), 1);
  clock.takeScrollControl();
  assert.equal(clock.scroll(.91), .91);
});
test('playback reaches the exact final state and stays there', () => {
  const clock = new ChapterClock();
  clock.seek(.99);
  clock.play();
  for (let frame = 0; frame < 20; frame++) clock.advance(32, 10000);
  assert.equal(clock.position, 1);
  assert.equal(clock.playing, false);
  assert.equal(clock.scroll(.97), 1);
  clock.play();
  assert.equal(clock.playing, false);
});
test('background-tab delay cannot skip the graph explanation', () => {
  const clock = new ChapterClock();
  clock.seek(.23);
  clock.play();
  assert.equal(clock.advance(300000, 10000), .2364);
});
test('direct navigation and reduced-motion seek stay within the scene', () => {
  const clock = new ChapterClock();
  assert.equal(clock.scroll(-.5), 0);
  assert.equal(clock.scroll(2), 1);
  assert.equal(clock.seek(NaN), 0);
  assert.equal(clock.seek(1), 1);
  assert.equal(clock.playing, false);
});
