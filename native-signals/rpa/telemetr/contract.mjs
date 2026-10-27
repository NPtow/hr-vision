import { createHash, randomUUID } from 'node:crypto';

export const isoNow = () => new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
export function resultCounter(text, emptyMessage) {
  const match = text?.match(/Найден(?:о)?\s*[≈~]?\s*([\d\s\u00a0'’]+)/);
  if (match) return Number(match[1].replace(/\D/g,''));
  return emptyMessage === 'Ничего не найдено' ? 0 : null;
}
export function validateInput(value) {
  if (!value || !Array.isArray(value.queries) || value.queries.length < 1 || value.queries.length > 409) throw new Error('Выберите от 1 до 409 компаний.');
  const validDate = s => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) && new Date(s+'T00:00:00Z').toISOString().startsWith(s);
  if (!validDate(value.dateFrom) || !validDate(value.dateTo) || value.dateTo < value.dateFrom || Date.parse(value.dateTo)-Date.parse(value.dateFrom) > 91*86400000) throw new Error('Период должен быть от 1 до 92 дней и доступен вашему тарифу.');
  if (!Number.isInteger(value.maxPages) || value.maxPages < 1 || value.maxPages > 10) throw new Error('Допустимо от 1 до 10 страниц на запрос.');
  const seen = new Set();
  const queries = value.queries.map(q => {
    if (!q || !/^[a-zA-Z0-9_-]{1,160}$/.test(q.companyID) || typeof q.companyName !== 'string' || !q.companyName.trim() || q.companyName.length > 300 || typeof q.query !== 'string' || !q.query.trim() || q.query.length > 300) throw new Error('Некорректная компания или поисковый запрос.');
    const key = q.companyID + '\0' + q.query.trim();
    if (seen.has(key)) throw new Error('Повтор компании и запроса в очереди.');
    seen.add(key);
    return {companyID:q.companyID, companyName:q.companyName.trim(), query:q.query.trim(), limit:value.maxPages*20};
  });
  return {queries, dateFrom:value.dateFrom, dateTo:value.dateTo, maxPages:value.maxPages};
}

export function searchURL(query, input) {
  const url = new URL('https://telemetr.me/posts');
  // These fields come from the observed public search form, not an internal API.
  Object.entries({apply_filters:'1',adv:'all',search_type:'all',network:'tg',text:query.query,date_from:input.dateFrom,date_to:input.dateTo,order_column:'relevance'}).forEach(([k,v])=>url.searchParams.set(k,v));
  return url.href;
}

export function checkedPageURL(raw, query, input, expectedPage) {
  let url; try { url = new URL(raw, 'https://telemetr.me'); } catch { return null; }
  if (url.origin !== 'https://telemetr.me' || !['/posts','/posts/','/all_posts','/all_posts/'].includes(url.pathname) || url.username || url.password) return null;
  if (url.searchParams.get('text') !== query.query || url.searchParams.get('network') !== 'tg' || url.searchParams.get('date_from') !== input.dateFrom || url.searchParams.get('date_to') !== input.dateTo || Number(url.searchParams.get('page') || '1') !== expectedPage) return null;
  if (query.searchType && url.searchParams.get('search_type') !== query.searchType) return null;
  return url.href;
}

export function telegramLink(raw, postID) {
  try {
    const u = new URL(raw);
    // Telemetr also renders invite-based links for indexed private channels.
    // Preserve the displayed link; do not invent a public username or access.
    if (u.protocol !== 'https:' || u.hostname !== 't.me' || u.username || u.password || !/^\/(?:[A-Za-z0-9_]+|c\/\d+|\+[A-Za-z0-9_-]+)\/\d+$/.test(u.pathname) || u.pathname.split('/').at(-1) !== postID) return null;
    return u.origin + u.pathname;
  } catch { return null; }
}

export function normalizeRows(rows, query, at = isoNow()) {
  const seen = new Set();
  return rows.map(row => {
    if (!/^\d+$/.test(row.channelID) || !/^\d+$/.test(row.postID) || typeof row.text !== 'string' || row.text.length > 100000 || typeof row.channelTitle !== 'string') throw new Error('Структура выдачи изменилась: запись не сохранена как пустой результат.');
    const key = row.channelID+':'+row.postID;
    if (seen.has(key)) throw new Error('Дубли публикаций на странице: нужна проверка разметки.');
    seen.add(key);
    return {companyID:query.companyID, companyName:query.companyName, query:query.query, platform:'telegram', channelID:row.channelID, postID:row.postID, channelTitle:row.channelTitle, text:row.text, ...(row.sourceURL && telegramLink(row.sourceURL,row.postID) ? {sourceURL:telegramLink(row.sourceURL,row.postID)}:{}), ...(row.dateLabel ? {sourceDateLabel:row.dateLabel}:{}), retrievedAt:at, origin:'telemetrWebsite', relevance:'unreviewed'};
  });
}

export const pageFingerprint = rows => createHash('sha256').update(rows.map(p=>p.channelID+':'+p.postID).sort().join('|')).digest('hex');
export function newJob(input) {
  return {version:1,id:'rpa-'+randomUUID(),createdAt:isoNow(),updatedAt:isoNow(),state:'queued',message:'В очереди',input:validateInput(input),entries:[],index:0};
}

export function newEntry(job) {
  const query = job.input.queries[job.index];
  return {query,runID:job.id+'-'+job.index,startedAt:isoNow(),posts:[],pages:[],page:1,nextURL:searchURL(query,job.input),totalCount:null,done:false};
}

export function asNativeRun(job, entry) {
  const complete = entry.done && entry.stopReason === 'end';
  return {version:1,id:entry.runID,startedAt:entry.startedAt,completedAt:isoNow(),origin:'telemetrWebsite',status:complete?'complete':'partial',queries:[{query:entry.query,posts:entry.posts,...(entry.totalCount !== null?{totalCount:entry.totalCount}:{}),rateLimitHeaders:{},rawResponseJSON:JSON.stringify({collector:'telemetr-rpa',version:1,period:[job.input.dateFrom,job.input.dateTo],pages:entry.pages,stopReason:entry.stopReason ?? job.state,coverage:'Rendered website results only. Affiliation unverified.'})}],...(complete?{}:{errorMessage:entry.stopReason==='pageLimit'?'Достигнут заданный предел страниц. Сохранена часть выдачи.':job.message})};
}
