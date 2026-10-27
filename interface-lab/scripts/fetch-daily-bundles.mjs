import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const version = '0.93.0';
const files = {
  'call-machine-object-bundle.js': '6613cf576406de7815e961b921c26b86bb7e116f7e8cc2d4f7e082c880e12856',
  'audio-processor-bundle.js': 'a0b6d2ad8b73b2daf6530e4e1ca8e82f720fa5c2f2c13a7b5209010575e45a21',
};
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), `../browser-assets/daily-call/${version}`);

async function digest(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

await mkdir(root, { recursive: true });
for (const [name, expected] of Object.entries(files)) {
  const destination = path.join(root, name);
  try {
    if (await digest(await readFile(destination)) === expected) continue;
  } catch { /* missing or unreadable: download */ }
  const url = `https://c.daily.co/call-machine/versioned/${version}/static/${name}`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Daily bundle ${name} responded ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  const actual = await digest(bytes);
  if (actual !== expected) throw new Error(`Daily bundle ${name} hash changed (${actual}). Update the pin with the daily-js version.`);
  await writeFile(destination, bytes);
  console.log(`saved ${name}`);
}
