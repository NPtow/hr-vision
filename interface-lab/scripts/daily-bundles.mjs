// Self-hosts Daily's call engine next to the app (served at /daily/<version>/) so a call does not depend on
// Daily's CDN being reachable from the user's network. Files are pinned by sha256 in daily-bundles.lock.json;
// a missing, changed or unexpected file fails the build. DAILY_BUNDLE_DIR may point at a local copy (offline build).
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export function readLock(file = path.join(root, 'daily-bundles.lock.json')) {
  const lock = JSON.parse(readFileSync(file, 'utf8'));
  if (!/^\d+\.\d+\.\d+$/.test(lock.version ?? '')) throw Error('daily-bundles.lock.json: bad version');
  const names = Object.keys(lock.files ?? {});
  if (!names.includes('call-machine-object-bundle.js') || !names.includes('audio-processor-bundle.js')) throw Error('daily-bundles.lock.json: both Daily bundles must be pinned');
  for (const [name, sha] of Object.entries(lock.files)) {
    if (!/^[a-z0-9-]+\.js$/.test(name) || !/^[a-f0-9]{64}$/.test(sha)) throw Error(`daily-bundles.lock.json: bad entry ${name}`);
  }
  return lock;
}

export function installedVersion() {
  return JSON.parse(readFileSync(path.join(root, 'node_modules/@daily-co/daily-js/package.json'), 'utf8')).version;
}

export function verify(name, data, lock) {
  const expected = lock.files[name];
  if (!expected) throw Error(`Unexpected Daily bundle ${name}`);
  const actual = createHash('sha256').update(data).digest('hex');
  if (actual !== expected) throw Error(`Daily bundle ${name} does not match its pinned sha256 (got ${actual})`);
  return data;
}

async function obtain(name, lock) {
  const local = process.env.DAILY_BUNDLE_DIR;
  if (local) return verify(name, readFileSync(path.join(local, name)), lock);
  const url = lock.source.replace('{version}', lock.version).replace('{file}', name);
  const response = await fetch(url, { redirect: 'error', signal: AbortSignal.timeout(120000) });
  if (!response.ok) throw Error(`Daily bundle ${name}: HTTP ${response.status}`);
  return verify(name, Buffer.from(await response.arrayBuffer()), lock);
}

export async function install(outDir, lock = readLock()) {
  const version = installedVersion();
  if (version !== lock.version) throw Error(`@daily-co/daily-js is ${version} but daily-bundles.lock.json pins ${lock.version}; update the lock`);
  const target = path.join(outDir, 'daily', lock.version);
  mkdirSync(target, { recursive: true });
  for (const name of Object.keys(lock.files)) {
    const file = path.join(target, name);
    if (existsSync(file)) { verify(name, readFileSync(file), lock); continue; }
    writeFileSync(file, await obtain(name, lock));
  }
  return target;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const outDir = process.argv[2];
  if (!outDir) throw Error('usage: node scripts/daily-bundles.mjs <build dir>');
  console.log(`Daily bundles in ${await install(path.resolve(outDir))}`);
}
