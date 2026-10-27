import http from 'node:http';
import { promises as fs } from 'node:fs';
import { existsSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from 'playwright-core';
import { extractPage } from './dom.mjs';
import { telemetrSessionCookies } from './session.mjs';
import { isoNow, newJob, newEntry, asNativeRun, checkedPageURL, normalizeRows, telegramLink, pageFingerprint } from './contract.mjs';

const root = process.env.TG_SIGNALS_RPA_DIR || path.join(os.homedir(),'Library/Application Support/TG Signals/rpa-telemetr');
const jobsDir = path.join(root,'jobs'), exportsDir = path.join(root,'exports');
for (const dir of [root,jobsDir,exportsDir,path.join(root,'browser-profile')]) { await fs.mkdir(dir,{recursive:true,mode:0o700}); await fs.chmod(dir,0o700); }
const lockPath = path.join(root,'server.lock');
try { await fs.writeFile(lockPath,String(process.pid),{flag:'wx',mode:0o600}); }
catch {
  const previous = Number(await fs.readFile(lockPath,'utf8').catch(()=>''));
  let active = false; try { process.kill(previous,0); active = previous > 1; } catch {}
  if (active) { console.error('RPA уже запущена.'); process.exit(1); }
  await fs.unlink(lockPath); await fs.writeFile(lockPath,String(process.pid),{flag:'wx',mode:0o600});
}
const atomic = async (file,value) => {
  const tmp = file+'.'+randomBytes(6).toString('hex')+'.tmp';
  await fs.writeFile(tmp,JSON.stringify(value,null,2),{mode:0o600});
  await fs.rename(tmp,file);
};
const jobs = new Map();
for (const name of await fs.readdir(jobsDir)) {
  if (!/^rpa-[a-f0-9-]+\.json$/.test(name)) continue;
  const job = JSON.parse(await fs.readFile(path.join(jobsDir,name),'utf8'));
  if (job.version !== 1 || !job.input || !Array.isArray(job.entries)) throw new Error('Журнал RPA повреждён. Исходный файл сохранён.');
  if (['running','queued'].includes(job.state)) { job.state='paused';job.message='Приложение было закрыто. Можно продолжить с сохранённой страницы.'; }
  jobs.set(job.id,job);
}
let context = null, page = null, opening = null, activeJob = null, worker = null;
const sessionFile=path.join(root,'browser-profile','telemetr-session.json');
let savingSession=false;
async function saveSession() {
  if (!context || savingSession) return;
  savingSession=true;
  try { await atomic(sessionFile,{version:1,cookies:telemetrSessionCookies(await context.cookies('https://telemetr.me'))}); }
  finally {savingSession=false;}
}
const save = async job => {job.updatedAt=isoNow();await atomic(path.join(jobsDir,job.id+'.json'),job);};
const exportEntry = async (job,entry) => { const run=asNativeRun(job,entry); if (Buffer.byteLength(JSON.stringify(run))>3_900_000) throw new Error('Сбор превышает 3,9 МБ. Уменьшите число страниц в следующем запуске.'); await atomic(path.join(exportsDir,entry.runID+'.json'),run); };
const summary = job => ({id:job.id,state:job.state,message:job.message,createdAt:job.createdAt,updatedAt:job.updatedAt,totalQueries:job.input.queries.length,finishedQueries:job.entries.filter(e=>e.done).length,posts:job.entries.reduce((n,e)=>n+e.posts.length,0),pages:job.entries.reduce((n,e)=>n+e.pages.length,0),companyName:job.input.queries[job.index]?.companyName ?? '',partialQueries:job.entries.filter(e=>e.done&&e.stopReason!=='end').length});

async function browser() {
  if (context) { if (!page || page.isClosed()) page=await context.newPage(); return page; }
  if (opening) return opening;
  opening = (async()=>{
    const browserChoice = existsSync(chromium.executablePath()) ? {} : {channel:'chrome'};
    context = await chromium.launchPersistentContext(path.join(root,'browser-profile'),{
      ...browserChoice, headless:false, viewport:{width:1280,height:900}, acceptDownloads:false, chromiumSandbox:true,
      ignoreDefaultArgs:['--disable-client-side-phishing-detection','--password-store=basic','--use-mock-keychain','--unsafely-disable-devtools-self-xss-warnings'],
    });
    try {
      const stored=JSON.parse(await fs.readFile(sessionFile,'utf8'));
      if(stored.version===1) await context.addCookies(telemetrSessionCookies(stored.cookies));
    } catch(error) { if(error.code!=='ENOENT') throw new Error('Не удалось прочитать сохранённый вход Telemetr. Исходный файл сохранён.'); }
    context.on('close',()=>{context=null;page=null;});
    page = context.pages()[0] || await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('dialog',d=>d.dismiss().catch(()=>{}));
    return page;
  })();
  try { return await opening; } finally {opening=null;}
}

async function captureSearch(query,input,pageNumber,url) {
  const p = await browser();
  if (!checkedPageURL(url,query,input,pageNumber)) throw new Error('Неожиданная ссылка следующей страницы. Сбор приостановлен.');
  const response = await p.goto(url,{waitUntil:'domcontentloaded',timeout:45000});
  if (response && [401,403,429].includes(response.status())) throw new Error('Сайт ограничил доступ. Очередь сохранена; проверьте браузер.');
  if (!['telemetr.me','www.telemetr.me'].includes(new URL(p.url()).hostname)) throw new Error('Нужен вход в Telemetr в открытом окне браузера.');
  const inputBox = p.getByRole('textbox',{name:'Поиск по тексту поста',exact:true});
  try { await inputBox.waitFor({state:'visible',timeout:15000}); }
  catch { throw new Error('Нужен вход или проверка в браузере Telemetr. После неё нажмите «Продолжить».'); }
  if (await inputBox.inputValue() !== query.query) throw new Error('Сайт изменил поисковый запрос. Очередь приостановлена.');
  await p.getByRole('button',{name:'Таблица',exact:true}).click();
  const snapshot = await p.evaluate(extractPage);
  if (snapshot.challenge || /лимит.{0,70}(исчерпан|достигнут|превышен)|слишком много запросов|подтвердите.{0,20}(робот|человек)|доступ.{0,30}тариф/isu.test(snapshot.alerts)) throw new Error('Лимит тарифа или проверка в браузере. Автоматические повторы остановлены.');
  if (snapshot.query !== query.query || snapshot.totalCount === null || !snapshot.period.includes(input.dateFrom) || !snapshot.period.includes(input.dateTo)) throw new Error('Не удалось подтвердить запрос, период или счётчик выдачи. Проверьте страницу.');
  if (snapshot.rows.length === 0 && snapshot.totalCount > 0) throw new Error('Сайт сообщил о результатах, но строки недоступны. Это не пустая выдача.');
  return snapshot;
}

async function originals(snapshot,job) {
  const p = await browser();
  for (const [index,row] of snapshot.rows.entries()) {
    if (job.state !== 'running') return false;
    // Use only IDs read from visible rows; hostile DOM cannot inject a selector.
    if (!/^\d+$/.test(row.channelID) || !/^\d+$/.test(row.postID)) throw new Error('Неизвестный формат ID публикации.');
    const locator = p.locator(`.ps-table tbody tr[data-item-id="${row.channelID}"][data-post-id="${row.postID}"]`);
    job.message=`${job.input.queries[job.index].companyName} · публикация ${index+1}/${snapshot.rows.length} на странице ${job.entries[job.index].page}`;
    await save(job);
    // A row's center can hit an embedded article link or the "more" control.
    // The observed date cell consistently opens Telemetr's own post card.
    await locator.locator('td:nth-child(2)').click();
    const modal = p.locator('#postModal');
    const link = modal.locator('a[title="Открыть в Telegram"]');
    try {
      await link.waitFor({state:'visible',timeout:25000});
      // Modal shell survives between posts. Wait for this post's actual link, never reuse the previous one.
      const deadline = Date.now()+10000;
      do {
        row.sourceURL = telegramLink(await link.getAttribute('href'),row.postID);
        const channel = await modal.locator('a[href^="/@"], a[href^="/joinchat/"]').first().innerText().catch(()=> '');
        if (row.sourceURL && channel.trim() === row.channelTitle.trim()) break;
        row.sourceURL = null;
        await delay(250);
      } while (Date.now()<deadline);
      if (!row.sourceURL) throw new Error('Не загрузилась оригинальная ссылка публикации.');
    } finally { await modal.getByRole('button',{name:'Закрыть',exact:true}).click().catch(()=>{}); }
    await delay(1000);
  }
  return true;
}

async function runJob(job) {
  activeJob=job.id; job.state='running';job.message='Открываю поиск Telemetr…';await save(job);
  try {
    while (job.index < job.input.queries.length && job.state === 'running') {
      let entry = job.entries[job.index];
      if (!entry) {entry=newEntry(job);job.entries.push(entry);await save(job);}
      if (entry.done) {job.index++;await save(job);continue;}
      job.message=`${entry.query.companyName} · страница ${entry.page}`;await save(job);
      const snapshot = await captureSearch(entry.query,job.input,entry.page,entry.nextURL);
      if (job.state !== 'running') break;
      const fingerprint=pageFingerprint(snapshot.rows);
      if (entry.pages.some(p=>p.fingerprint===fingerprint) && snapshot.rows.length) throw new Error('Повторилась уже сохранённая страница. Сбор остановлен, дубли не добавлены.');
      if (!(await originals(snapshot,job))) break;
      const incoming = normalizeRows(snapshot.rows,entry.query);
      const seen=new Set(entry.posts.map(p=>p.channelID+':'+p.postID));
      entry.posts.push(...incoming.filter(p=>!seen.has(p.channelID+':'+p.postID)));
      entry.totalCount=snapshot.totalCount;
      entry.pages.push({page:entry.page,url:snapshot.url,query:entry.query.query,period:snapshot.period,counter:snapshot.counter,approximate:snapshot.approximate,received:incoming.length,fingerprint,retrievedAt:isoNow()});
      const next = snapshot.links.find(a=>a.text===String(entry.page+1));
      if (!next) {
        entry.done=true;entry.stopReason=(!snapshot.approximate && entry.posts.length>=snapshot.totalCount)?'end':'siteLimit';
      } else if (entry.page >= job.input.maxPages) {entry.done=true;entry.stopReason='pageLimit';}
      else {
        const checked=checkedPageURL(next.url,entry.query,job.input,entry.page+1);
        if (!checked) throw new Error('Пагинация изменила запрос или период. Данные сохранены, нужна проверка.');
        entry.page++;entry.nextURL=checked;
      }
      // Save durable page data before advancing the query. Recovering exports never repeats a committed page.
      await save(job);await exportEntry(job,entry);
      await saveSession();
      if (entry.done) {job.index++;await save(job);}
      if (job.state==='running' && job.index < job.input.queries.length) await delay(5000);
    }
    if (job.state==='running') {job.state='complete';job.message='Сбор завершён. Проверьте связь публикаций с компаниями.';}
  } catch(error) {
    job.state='attention';
    // Do not leak browser URLs with auth state, cookies, stack traces or raw responses.
    job.message=error.message.startsWith('page.') || /Timeout|Target.*closed|ECONN|browserType/i.test(error.message)
      ? 'Браузер не завершил действие. Откройте его, проверьте вход и продолжите очередь.'
      : error.message.slice(0,400);
  } finally {
    await save(job);
    for (const entry of job.entries) if (entry.pages.length) await exportEntry(job,entry);
    activeJob=null;worker=null;
  }
}

const token = randomBytes(32).toString('hex');
function authorized(req) {const supplied=Buffer.from(req.headers.authorization??'');const expected=Buffer.from('Bearer '+token);return supplied.length===expected.length&&timingSafeEqual(supplied,expected);}
const server = http.createServer(async(req,res)=>{
  const reply=(code,value)=>{res.writeHead(code,{'content-type':'application/json; charset=utf-8','cache-control':'no-store'});res.end(JSON.stringify(value));};
  // No CORS: arbitrary websites cannot instruct this signed-in local browser.
  if (req.headers.origin || req.headers.host!==`127.0.0.1:${server.address().port}` || !authorized(req)) return reply(403,{error:'Нет доступа к локальному сборщику.'});
  try {
    const url=new URL(req.url,'http://127.0.0.1');
    if (req.method==='GET'&&url.pathname==='/status') return reply(200,{version:1,browserOpen:!!page&&!page.isClosed(),activeJob,jobs:[...jobs.values()].sort((a,b)=>b.createdAt.localeCompare(a.createdAt)).slice(0,30).map(summary)});
    if (req.method==='POST'&&url.pathname==='/browser') {
      const p=await browser(); if(!p.url().startsWith('https://telemetr.me/')) await p.goto('https://telemetr.me/posts?adv=all',{waitUntil:'domcontentloaded'});
      await p.bringToFront();return reply(200,{ok:true});
    }
    if (req.method==='POST'&&url.pathname==='/jobs') {
      if(worker) return reply(409,{error:'Дождитесь завершения текущей страницы или поставьте сбор на паузу.'});
      let body='';for await(const chunk of req){body+=chunk;if(Buffer.byteLength(body)>500000) return reply(413,{error:'Слишком большой список компаний.'});}
      const job=newJob(JSON.parse(body));jobs.set(job.id,job);await save(job);worker=runJob(job);return reply(202,summary(job));
    }
    const match=url.pathname.match(/^\/jobs\/(rpa-[a-f0-9-]+)\/(pause|resume)$/);
    if(req.method==='POST'&&match){
      const job=jobs.get(match[1]);if(!job)return reply(404,{error:'Сбор не найден.'});
      if(match[2]==='pause') {job.state='paused';job.message='Пауза. Сохранённые страницы доступны в результатах.';await save(job);return reply(200,summary(job));}
      if(worker)return reply(409,{error:'Предыдущее действие браузера ещё завершается.'});
      if(job.state==='complete')return reply(409,{error:'Сбор уже завершён.'});
      // Re-create missing exports from durable page data before navigating further.
      for(const entry of job.entries)if(entry.pages.length)await exportEntry(job,entry);
      worker=runJob(job);return reply(202,summary(job));
    }
    reply(404,{error:'Неизвестный метод RPA.'});
  } catch {reply(400,{error:'Не удалось выполнить действие. Проверьте параметры и открытое окно браузера.'});}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
await atomic(path.join(root,'control.json'),{version:1,pid:process.pid,port:server.address().port,token});
console.log('TG Signals RPA готова. Управление доступно приложению на этом Mac.');
const sessionTimer=setInterval(()=>{if(context)void saveSession().catch(()=>{});},5000);
sessionTimer.unref();
async function shutdown(){
  clearInterval(sessionTimer);
  for(const job of jobs.values())if(job.state==='running'){job.state='paused';job.message='Сборщик закрыт. Очередь сохранена.';await save(job);}
  await saveSession().catch(()=>{});
  await context?.close().catch(()=>{});server.close();await fs.unlink(lockPath).catch(()=>{});await fs.unlink(path.join(root,'control.json')).catch(()=>{});process.exit(0);
}
process.on('SIGTERM',()=>void shutdown());process.on('SIGINT',()=>void shutdown());
