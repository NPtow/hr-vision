import test from 'node:test';
import assert from 'node:assert/strict';
import { fitSceneFrame } from '../src/useSceneFrame.ts';

test('short phones retain readable type and the full touch target size', () => {
  // 320×568 and 390×844 screens after the existing 14px side insets,
  // header clearance and bottom inset.
  for (const [width, height] of [[292, 456], [362, 732]]) {
    const frame = fitSceneFrame(width, height, true);
    assert.equal(frame.width * frame.scale, width);
    assert.equal(frame.height * frame.scale, height);
    assert.equal(44 * frame.scale, 44, 'a 44px control must not become a miniature');
    assert.equal(16 * frame.scale, 16, 'body text must retain its authored size');
  }
});

test('mobile rotation changes the viewport, not the visual scale of controls', () => {
  const portrait = fitSceneFrame(292, 456, true);
  const shortViewport = fitSceneFrame(540, 240, true);
  assert.equal(shortViewport.scale, portrait.scale);
  assert.equal(shortViewport.height, 240);
  assert.equal(shortViewport.width, 540);
});

test('desktop scenes keep their aspect ratio and fit without enlargement', () => {
  for (const [width, height] of [[1280, 900], [880, 500], [740, 880]]) {
    const frame = fitSceneFrame(width, height, false, 1224, 666);
    assert.equal(frame.width / frame.height, 1224 / 666);
    assert.ok(frame.width * frame.scale <= width);
    assert.ok(frame.height * frame.scale <= height);
    assert.ok(frame.scale <= 1);
  }
  assert.equal(fitSceneFrame(1500, 900, false).scale, 1);
});
