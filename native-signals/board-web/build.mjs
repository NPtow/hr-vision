import { build } from 'esbuild';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = path.dirname(fileURLToPath(import.meta.url));
const outdir = path.resolve(root, '../Sources/SignalsApp/Resources/Board');
await mkdir(outdir, { recursive: true });
await build({ absWorkingDir: root, entryPoints: ['src/main.tsx'], outfile: path.join(outdir, 'board.js'), bundle: true, format: 'iife', platform: 'browser', target: ['safari17'], minify: true, legalComments: 'eof', define: { 'process.env.NODE_ENV': '"production"' } });
await writeFile(path.join(outdir, 'index.html'), `<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>Разбор компании</title><link rel="stylesheet" href="board.css"></head><body><div id="root"></div><script src="board.js"></script></body></html>\n`);
const pkg = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
const packages = ['@xyflow/react', '@xyflow/system', 'react', 'react-dom', 'scheduler', 'use-sync-external-store', 'zustand', 'classcat', 'd3-dispatch', 'd3-drag', 'd3-selection', 'd3-transition', 'd3-zoom', 'd3-interpolate', 'd3-color', 'd3-timer', 'd3-ease'];
const notices = [];
for (const name of packages) {
  const directory = path.join(root, 'node_modules', name);
  for (const filename of ['LICENSE', 'LICENSE.md', 'LICENSE.txt']) {
    try { notices.push(`${name}\n${'='.repeat(name.length)}\n${await readFile(path.join(directory, filename), 'utf8')}`); break; } catch {}
  }
}
await writeFile(path.join(outdir, 'THIRD-PARTY-NOTICES.txt'), notices.join('\n\n'));
const hashes = {};
for (const name of ['index.html', 'board.js', 'board.css', 'THIRD-PARTY-NOTICES.txt']) hashes[name] = createHash('sha256').update(await readFile(path.join(outdir, name))).digest('hex');
await writeFile(path.join(outdir, 'manifest.json'), JSON.stringify({ protocolVersion: 1, dependencies: pkg.dependencies, sha256: hashes }, null, 2) + '\n');
console.log(`Built local board resources in ${outdir}`);
