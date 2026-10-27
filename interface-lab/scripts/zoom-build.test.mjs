// Deployment contract: the release includes the complete, internally consistent SDK.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
const base = new URL('../zoom-client/dist/call/zoom/', import.meta.url);
const manifest = JSON.parse(readFileSync(new URL('bootstrap.json', base)));
test('pinned SDK build contains its full resource manifest, local helper pages and UI', () => {
  assert.equal(manifest.version, '6.10.0');
  for (const [file, expected] of Object.entries(manifest.files)) {
    assert.ok(!file.includes('..') && !file.startsWith('/'));
    const data = readFileSync(new URL(file, base));
    assert.equal(data.length, expected.bytes, file);
    assert.equal(createHash('sha256').update(data).digest('hex'), expected.sha256, file);
  }
  for (const file of ['client.html', 'left.html', 'helper.html', 'externalLinkPage.html',
    'vendor/6.10.0/ui/zoom-meetingsdk.css', 'vendor/6.10.0/lib/lang/ru-RU.json',
    'vendor/6.10.0/lib/av/audio.simd.wasm', 'vendor/6.10.0/lib/av/audio.encode.wasm',
    'vendor/6.10.0/lib/av/video.mtsimd.wasm', 'vendor/6.10.0/lib/av/video.mt.wasm',
    'vendor/6.10.0/lib/av/video.simd.wasm', 'vendor/6.10.0/lib/av/video.decode.wasm']) assert.ok(manifest.files[file], file);
  assert.ok(manifest.warmup.some(file => /^assets\/zoommtgMain-.*\.js$/.test(file)));
  for (const file of manifest.warmup) assert.ok(manifest.files[file], file);
  const html = readFileSync(new URL('client.html', base), 'utf8');
  assert.equal(/type=["']importmap|https:\/\/source.zoom.us/.test(html), false);
});
