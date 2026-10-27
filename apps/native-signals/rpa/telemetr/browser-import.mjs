// Import rendered-page snapshots captured through Codex's existing browser.
// This module never opens a browser, reads a session or calls Telemetr's API.
import { normalizeRows, checkedPageURL, telegramLink, pageFingerprint, isoNow, resultCounter } from './contract.mjs';
import { readFile, readdir, mkdir, rename, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { homedir } from 'node:os';
import { pathToFileURL } from 'node:url';

export function browserRun(manifest, pages) {
  if (!/^rpa-browser-[a-zA-Z0-9_-]+$/.test(manifest.id) || !Array.isArray(manifest.queries) || !manifest.queries.length) throw Error('Invalid browser run');
  const results = [];
  let completeCount = 0, pageCount = 0;
  for (const query of manifest.queries) {
    const selected = pages.filter(p => p.companyID === query.companyID && p.query === query.query).sort((a,b) => a.page-b.page);
    const posts = new Map(), receipts = [];
    let validEnd = false;
    for (let index = 0; index < selected.length; index++) {
      const page = selected[index];
      if (page.page !== index+1 || !checkedPageURL(page.url, query, manifest, page.page)) throw Error('Page order or query mismatch');
      if (page.challenge || !Array.isArray(page.rows) || !Array.isArray(page.capturedRows)) throw Error('Unverified page');
      if (!Number.isInteger(page.totalCount) || page.totalCount < 0) throw Error('Missing result counter');
      if (page.totalCount !== resultCounter(page.counter,page.emptyMessage)) throw Error('Result counter does not match rendered text');
      const expected = new Set(page.rows.map(r => r.channelID+':'+r.postID));
      const captured = new Set(page.capturedRows.map(r => r.channelID+':'+r.postID));
      if (captured.size !== page.capturedRows.length || expected.size !== page.rows.length || [...captured].some(k=>!expected.has(k))) throw Error('Duplicated or mismatched card');
      const cards = new Map(page.capturedRows.map(r=>[r.channelID+':'+r.postID,r]));
      const importedRows = page.captureMode === 'table' ? page.rows.map(r=>cards.get(r.channelID+':'+r.postID) ?? {...r,retrievedAt:page.retrievedAt,textFromCard:false}) : page.capturedRows;
      let duplicates = 0;
      for (const row of importedRows) {
        const fromCard = row.textFromCard !== false;
        if (fromCard && (row.sourceURL ? !telegramLink(row.sourceURL,row.postID) : row.deleted !== true)) throw Error('Missing original link or archive status');
        if (!fromCard && (row.sourceURL || row.deleted !== undefined)) throw Error('Table fragment cannot assert original or deletion status');
        const at = row.retrievedAt?.replace(/\.\d{3}Z$/, 'Z');
        if (!at || !Number.isFinite(Date.parse(at))) throw Error('Missing capture date');
        const post = normalizeRows([row], query, at)[0];
        Object.assign(post, {sourcePageURL:page.url,textFromCard:fromCard,...(fromCard?{sourceDeleted:row.deleted === true}:{})});
        const key = post.channelID+':'+post.postID;
        const previous = posts.get(key);
        if (previous) duplicates++;
        // Live ranking can repeat a row at a page boundary. Count it once and
        // retain the expanded card, while the counter keeps missing coverage visible.
        if (!previous || (post.textFromCard && !previous.textFromCard)) posts.set(key,post);
      }
      receipts.push({page:page.page,url:page.url,counter:page.counter,rows:page.rows.length,captured:importedRows.length,duplicates,fullCards:page.capturedRows.length,fingerprint:pageFingerprint(importedRows)});
      const hasNext = page.links.some(link => checkedPageURL(link.url,query,manifest,page.page+1));
      const countersStable = selected.every(p=>p.totalCount===page.totalCount && !p.approximate);
      validEnd = !hasNext && countersStable && posts.size===page.totalCount && expected.size===importedRows.length;
    }
    if(validEnd) completeCount++;
    pageCount += selected.length;
    results.push({query:{...query,limit:200},posts:[...posts.values()],...(selected.length?{totalCount:selected[0].totalCount}:{}),rateLimitHeaders:{},
      rawResponseJSON:JSON.stringify({collector:'codex-browser-rpa',version:2,period:[manifest.dateFrom,manifest.dateTo],pages:receipts,complete:validEnd,searchType:query.searchType,scope:'Rendered search results. Table fragments are explicitly distinguished from expanded cards; affiliations unreviewed; media and comments excluded.'}),
      ...(!validEnd?{errorMessage:manifest.finished?'Сохранена доступная выдача; счётчик Telemetr больше числа полученных записей.':'Сбор выдачи ещё не завершён.'}:{})});
  }
  const complete = completeCount===manifest.queries.length;
  const count = results.reduce((n,r)=>n+r.posts.length,0);
  return {run:{version:1,id:manifest.id,title:manifest.title,startedAt:manifest.startedAt,completedAt:isoNow(),origin:'telemetrWebsite',status:complete?'complete':'partial',queries:results,...(!complete?{errorMessage:manifest.finished?'Проход завершён. Часть запросов ограничена доступной выдачей сайта; подробности по компаниям ниже.':'Сохранены полученные страницы. Сбор продолжается через браузер Codex.'}:{})},
    progress:{id:manifest.id,state:complete?'complete':manifest.finished?'partial':'running',message:(manifest.title?manifest.title+' · ':'')+(complete?'Сбор через открытый браузер завершён':manifest.finished?'Доступные страницы собраны':'Сбор через открытый браузер'),totalQueries:manifest.queries.length,finishedQueries:manifest.finished?results.filter(r=>r.totalCount!==undefined).length:completeCount,posts:count,pages:pageCount,companyName:'',partialQueries:manifest.queries.length-completeCount}};
}

async function atomic(path, data) {
  await writeFile(path+'.tmp',JSON.stringify(data,null,2),{mode:0o600});
  await rename(path+'.tmp',path);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const source = resolve(process.argv[2]);
  const manifest = JSON.parse(await readFile(join(source,'manifest.json'),'utf8'));
  const pages = [];
  for (const file of await readdir(source)) if (/^[a-z0-9_-]+-\d+\.json$/.test(file)) pages.push(JSON.parse(await readFile(join(source,file),'utf8')));
  const {run,progress} = browserRun(manifest,pages);
  const root = process.env.TG_SIGNALS_RPA_DIR || join(homedir(),'Library/Application Support/TG Signals/rpa-telemetr');
  for (const dir of ['exports','browser-jobs']) await mkdir(join(root,dir),{recursive:true,mode:0o700});
  await atomic(join(root,'exports',run.id+'.json'),run);
  await atomic(join(root,'browser-jobs',run.id+'.json'),progress);
  console.log(JSON.stringify(progress));
}
