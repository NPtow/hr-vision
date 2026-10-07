import { readFileSync, writeFileSync, readdirSync, mkdirSync, copyFileSync, lstatSync, rmSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

const target = process.env.HR_BUILD_TARGET;
const branch = process.env.GITHUB_REF_NAME;
const sha = process.env.GITHUB_SHA;
const run = `${process.env.GITHUB_RUN_ID}.${process.env.GITHUB_RUN_ATTEMPT}`;
if (!['service', 'mockups'].includes(target) || !/^[a-f0-9]{40}$/.test(sha ?? '') || !/^\d+\.\d+$/.test(run)) throw Error('Missing release identity');
if (branch !== (target === 'service' ? 'main' : 'codex/mockups')) throw Error('Branch and destination differ');
const build = 'storybook-static';
const entries = JSON.parse(readFileSync(`${build}/index.json`)).entries;
const ids = Object.keys(entries);
if (target === 'service') {
  for (const id of ['start', 'manager', 'candidate']) if (!entries[`hr-vision-product--${id}`]) throw Error(`Missing ${id}`);
  if (ids.some(id => !id.startsWith('hr-vision-product--'))) throw Error('Mockup story in service build');
} else {
  if (!entries['hr-vision-employer-menu--gallery']) throw Error('Missing employer gallery');
  if (ids.some(id => id.startsWith('hr-vision-product--'))) throw Error('Connected service in mockup build');
}
const stage = 'release-stage';
rmSync(stage, { recursive: true, force: true });
mkdirSync(`${stage}/ui`, { recursive: true });
const files = {};
const digest = data => createHash('sha256').update(data).digest('hex');
function add(source, name) {
  const data = readFileSync(source);
  files[name] = digest(data);
  mkdirSync(path.dirname(`${stage}/${name}`), { recursive: true });
  copyFileSync(source, `${stage}/${name}`);
}
function visit(dir = '') {
  for (const name of readdirSync(`${build}/${dir}`)) {
    const rel = dir + name, file = `${build}/${rel}`, info = lstatSync(file);
    if (info.isSymbolicLink()) throw Error(`Symlink in build: ${rel}`);
    if (info.isDirectory()) { visit(`${rel}/`); continue; }
    if (!( /\.(js|css|svg|woff2?|ttf|png|jpe?g|webp|ico|gif)$/.test(rel)
      || ['index.html', 'iframe.html', 'index.json', 'project.json'].includes(rel)
      || (target === 'mockups' && rel === 'presentations/hrds-2026-10-07/index.html')
      || /\.LICENSE\.txt$/.test(rel))) throw Error(`Unexpected public file: ${rel}`);
    add(file, `ui/${rel}`);
  }
}
visit();
const manifest = { version: 1, target, branch, sha, run, release: `${sha}-${run}`, files };
const deployment = JSON.stringify({ target, branch, sha, run }) + '\n';
writeFileSync(`${stage}/ui/deployment.json`, deployment);
files['ui/deployment.json'] = digest(deployment);
if (target === 'service') for (const name of ['app.py', 'browser_access.py', 'daily_media.py', 'domain.py', 'dsa_panel.py', 'feedback.py']) add(`server/${name}`, `api/${name}`);
writeFileSync(`${stage}/release.json`, JSON.stringify(manifest) + '\n');
execFileSync('tar', ['-czf', 'release.tgz', '-C', stage, 'release.json', 'ui', ...(target === 'service' ? ['api'] : [])], {
  env: { ...process.env, COPYFILE_DISABLE: '1' },
});
console.log(`Packaged ${target}: ${ids.length} stories, ${Object.keys(files).length} files, commit ${sha}`);
