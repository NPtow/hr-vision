import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { parse } from '@babel/parser';

// Only literals shipped with the UI can be readable. Text returned by the API
// or entered by a user never expands this vocabulary at runtime.
export function extractStaticCopy(source, filename = 'component.tsx') {
  const values = new Set();
  const add = text => {
    const normalized = text.replace(/\s+/g, ' ').trim();
    if (normalized.length <= 1500 && /\p{L}/u.test(normalized)
      && !/https?:|@|\b(?:token|secret|password)=/i.test(normalized)) values.add(normalized);
  };
  const tree = parse(source, {sourceType:'module', plugins: filename.endsWith('.tsx') ? ['typescript','jsx'] : ['typescript']});
  const visit = node => {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'JSXText' || node.type === 'StringLiteral') add(node.value);
    if (node.type === 'TemplateLiteral' && node.expressions.length === 0) add(node.quasis[0].value.cooked ?? '');
    for (const [key, child] of Object.entries(node)) {
      if (['loc','start','end','comments','tokens','extra'].includes(key)) continue;
      if (Array.isArray(child)) child.forEach(visit);
      else if (child && typeof child === 'object') visit(child);
    }
  };
  visit(tree);
  return [...values];
}

export function collectStaticCopy(entries) {
  const values = new Set(['МашаДаша', 'HR Vision']);
  const visited = new Set();
  const visit = path => {
    if (visited.has(path)) return;
    visited.add(path);
    const source = readFileSync(path, 'utf8');
    for (const text of extractStaticCopy(source, path)) values.add(text);
    // Follow local production imports, including lazy components. Mockup-only
    // scenes and unrelated fixtures cannot add words to the public vocabulary.
    const tree = parse(source, {sourceType:'module', plugins: path.endsWith('.tsx') ? ['typescript','jsx'] : ['typescript']});
    const follow = node => {
      if (!node || typeof node !== 'object') return;
      const dependency = ['ImportDeclaration','ExportNamedDeclaration','ExportAllDeclaration'].includes(node.type) ? node.source?.value
        : node.type === 'CallExpression' && node.callee?.type === 'Import' ? node.arguments[0]?.value : undefined;
      if (dependency?.startsWith('.')) {
        const base = resolve(dirname(path), dependency);
        const target = [base,base+'.tsx',base+'.ts',base+'/index.tsx',base+'/index.ts'].find(file => /\.tsx?$/.test(file) && existsSync(file));
        if (target) visit(target);
      }
      for (const [key, child] of Object.entries(node)) {
        if (['loc','start','end','comments','tokens','extra'].includes(key)) continue;
        if (Array.isArray(child)) child.forEach(follow);
        else if (child && typeof child === 'object') follow(child);
      }
    };
    follow(tree);
  };
  entries.forEach(entry => visit(resolve(entry)));
  return [...values].sort();
}
