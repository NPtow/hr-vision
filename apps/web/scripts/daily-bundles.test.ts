import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { DAILY_JS_VERSION, dailyCallConfig } from '../src/scenes/candidate-portal/call-connection.ts';
// @ts-expect-error plain ESM build script
import { readLock, verify, install } from './daily-bundles.mjs';

const root = path.resolve(import.meta.dirname, '..');
const lock = readLock();

test('the pinned Daily bundles, the client constant and package.json name the same version', () => {
  const pkg = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8'));
  assert.equal(pkg.dependencies['@daily-co/daily-js'], lock.version);
  assert.equal(DAILY_JS_VERSION, lock.version);
  assert.match(pkg.scripts.build, /node scripts\/daily-bundles\.mjs storybook-static$/);
});

test('calls load the call engine from this origin, never from the Daily CDN', () => {
  assert.deepEqual(dailyCallConfig('https://mashadasha.tech'), { bundlePathOverride: `https://mashadasha.tech/daily/${lock.version}` });
  for (const file of ['src/scenes/recruiter-portal/RecruiterCall.tsx', 'src/scenes/candidate-portal/InterviewRoom.tsx']) {
    const source = readFileSync(path.join(root, file), 'utf8');
    const calls = source.split('createCallObject({').slice(1);
    assert.equal(calls.length, 1, file);
    assert.match(calls[0].slice(0, 120), /^\s*dailyConfig: dailyCallConfig\(\),/, file);
  }
});

test('a bundle whose bytes differ from the pin is refused, the pinned bytes are accepted', () => {
  const fake = { ...lock, files: { 'call-machine-object-bundle.js': createHash('sha256').update('engine').digest('hex'), 'audio-processor-bundle.js': lock.files['audio-processor-bundle.js'] } };
  assert.equal(verify('call-machine-object-bundle.js', Buffer.from('engine'), fake).toString(), 'engine');
  assert.throws(() => verify('call-machine-object-bundle.js', Buffer.from('engine!'), fake), /does not match its pinned sha256/);
  assert.throws(() => verify('other.js', Buffer.from('x'), fake), /Unexpected Daily bundle/);
});

test('install copies verified bundles into <build>/daily/<version>/ and refuses a tampered local copy', async () => {
  const source = mkdtempSync(path.join(tmpdir(), 'daily-src-'));
  const fake = { ...lock, files: { 'call-machine-object-bundle.js': createHash('sha256').update('engine').digest('hex'), 'audio-processor-bundle.js': createHash('sha256').update('audio').digest('hex') } };
  writeFileSync(path.join(source, 'call-machine-object-bundle.js'), 'engine');
  writeFileSync(path.join(source, 'audio-processor-bundle.js'), 'audio');
  process.env.DAILY_BUNDLE_DIR = source;
  try {
    const out = mkdtempSync(path.join(tmpdir(), 'daily-out-'));
    const target = await install(out, fake);
    assert.equal(target, path.join(out, 'daily', lock.version));
    assert.equal(readFileSync(path.join(target, 'call-machine-object-bundle.js'), 'utf8'), 'engine');
    writeFileSync(path.join(source, 'audio-processor-bundle.js'), 'tampered');
    const again = mkdtempSync(path.join(tmpdir(), 'daily-out-'));
    await assert.rejects(install(again, fake), /does not match its pinned sha256/);
    assert.equal(existsSync(path.join(again, 'daily', lock.version, 'audio-processor-bundle.js')), false);
  } finally { delete process.env.DAILY_BUNDLE_DIR; }
});
