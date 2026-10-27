import { defineConfig } from 'vite';
import { cpSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(fileURLToPath(import.meta.url));
const sdk = path.join(root, 'node_modules/@zoom/meetingsdk');
const version = JSON.parse(readFileSync(path.join(sdk, 'package.json'))).version;
if (version !== '6.10.0') throw new Error('Review the asset layout before upgrading Zoom');
const output = path.join(root, 'dist/call/zoom');
let warmup = [];
export default defineConfig({
  root: path.join(root, 'src'), base: '/call/zoom/', publicDir: false,
  build: { outDir: output, emptyOutDir: true, target: 'es2022',
    rolldownOptions: { input: { client: path.join(root, 'src/client.html'), left: path.join(root, 'src/left.html') } } },
  plugins: [{
    name: 'zoom-owned-assets',
    generateBundle(_, bundle) {
      // Warm the main UI's static import graph without executing it before join.
      const files = new Set();
      const visit = name => { if (files.has(name)) return; files.add(name); const item = bundle[name]; if (item?.type === 'chunk') item.imports.forEach(visit); };
      for (const item of Object.values(bundle)) if (item.type === 'chunk' && /zoommtgMain/.test(item.name)) visit(item.fileName);
      warmup = [...files].filter(name => name.endsWith('.js'));
    },
    closeBundle() {
      const vendor = path.join(output, 'vendor', version);
      mkdirSync(vendor, { recursive: true });
      for (const dir of ['lib', 'ui']) cpSync(path.join(sdk, 'dist', dir), path.join(vendor, dir), { recursive: true });
      for (const file of ['helper.html', 'externalLinkPage.html']) cpSync(path.join(sdk, file), path.join(output, file));
      for (const file of ['LICENSE.md', 'oss_attribution.txt']) cpSync(path.join(sdk, file), path.join(vendor, file + '.LICENSE.txt'));
      const prefix = `vendor/${version}/`;
      warmup.push(...['lib/lang/ru-RU.json', 'lib/webim.min.js', 'lib/av/tp.min.js', 'lib/av/js_media.min.js'].map(name => prefix + name));
      const files = {};
      const scan = (dir, prefix = '') => {
        for (const name of readdirSync(dir)) {
          const file = path.join(dir, name), rel = prefix + name;
          if (statSync(file).isDirectory()) scan(file, rel + '/');
          else files[rel] = { bytes: statSync(file).size, sha256: createHash('sha256').update(readFileSync(file)).digest('hex') };
        }
      };
      scan(output);
      for (const name of warmup) if (!files[name]) throw new Error(`Missing Zoom warmup asset: ${name}`);
      writeFileSync(path.join(output, 'bootstrap.json'), JSON.stringify({ version, warmup, files }));
    },
  }],
});
