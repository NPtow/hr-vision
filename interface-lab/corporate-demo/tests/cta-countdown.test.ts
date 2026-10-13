import test from 'node:test';
import assert from 'node:assert/strict';
import { ctaProgressAt, ctaSnapshot } from '../src/cta-offer.ts';

test('50% stays fixed for ten seconds, with no visible timer', () => {
  const firstOffer = { seconds:30, discount:50, expired:false, timerVisible:false };
  assert.deepEqual(ctaSnapshot(0), firstOffer);
  assert.deepEqual(ctaSnapshot(9.999 / 40), firstOffer);
});
test('at ten seconds the offer changes to a fixed 30% for thirty seconds', () => {
  assert.deepEqual(ctaSnapshot(10 / 40), { seconds:30, discount:30, expired:false, timerVisible:true });
  assert.deepEqual(ctaSnapshot(20 / 40), { seconds:20, discount:30, expired:false, timerVisible:true });
  assert.deepEqual(ctaSnapshot(39.999 / 40), { seconds:1, discount:30, expired:false, timerVisible:true });
  assert.deepEqual(ctaSnapshot(1), { seconds:0, discount:0, expired:true, timerVisible:false });
  assert.deepEqual(ctaSnapshot(1.2), ctaSnapshot(1));
});
test('scrubbing back restores the offer, including after expiry', () => {
  const forward = [0,.25,.5,.75,1].map(ctaSnapshot);
  const backward = [1,.75,.5,.25,0].map(ctaSnapshot).reverse();
  assert.deepEqual(backward, forward);
  assert.deepEqual(ctaSnapshot(-.2), ctaSnapshot(0));
  assert.deepEqual(ctaSnapshot(Number.NaN), ctaSnapshot(0));
});

test('offer clock uses elapsed time after its trigger, including delayed frames', () => {
  assert.equal(ctaSnapshot(ctaProgressAt(9_999)).discount, 50);
  assert.deepEqual(ctaSnapshot(ctaProgressAt(10_000)), { seconds:30, discount:30, expired:false, timerVisible:true });
  assert.deepEqual(ctaSnapshot(ctaProgressAt(31_250)), { seconds:9, discount:30, expired:false, timerVisible:true });
  assert.deepEqual(ctaSnapshot(ctaProgressAt(40_000)), ctaSnapshot(1));
  assert.deepEqual(ctaSnapshot(ctaProgressAt(300_000)), ctaSnapshot(1));
  assert.equal(ctaProgressAt(-100), 0);
  assert.equal(ctaProgressAt(Number.NaN), 0);
});
