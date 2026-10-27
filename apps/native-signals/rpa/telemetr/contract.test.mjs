import test from 'node:test';
import assert from 'node:assert/strict';
import { validateInput, searchURL, checkedPageURL, telegramLink, normalizeRows, pageFingerprint, newJob, newEntry, asNativeRun } from './contract.mjs';
const input = {queries:[{companyID:'karpov',companyName:'karpov.courses',query:'karpov.courses'}],dateFrom:'2026-09-11',dateTo:'2026-10-11',maxPages:3};

test('query and date window survive search and actual pagination URL',()=>{
  const value=validateInput(input),q=value.queries[0];
  assert.equal(checkedPageURL(searchURL(q,value),q,value,1),searchURL(q,value));
  const next='https://telemetr.me/all_posts/?page=2&text=karpov.courses&date_from=2026-09-11&date_to=2026-10-11&network=tg&availablePeriodDays=92';
  assert.ok(checkedPageURL(next,q,value,2));
  for(const altered of [next.replace('telemetr.me','evil.test'),next.replace('page=2','page=3'),next.replace('karpov.courses','SETTERS'),next.replace('2026-09-11','2020-09-11'),next.replace('network=tg','network=max')]) assert.equal(checkedPageURL(altered,q,value,2),null);
});
test('refuses duplicate queries, invalid dates, unlimited pages and traversal IDs',()=>{
  for(const altered of [{...input,maxPages:11},{...input,dateFrom:'2025-01-01'},{...input,dateTo:'2026-99-99'},{...input,queries:[...input.queries,...input.queries]},{...input,queries:[{...input.queries[0],companyID:'../outside'}]}]) assert.throws(()=>validateInput(altered));
});
test('original URL must belong to the current post',()=>{
  assert.equal(telegramLink('https://t.me/cpa_seti/1702','1702'),'https://t.me/cpa_seti/1702');
  assert.equal(telegramLink('https://t.me/+ExampleInvite/3033','3033'),'https://t.me/+ExampleInvite/3033');
  assert.equal(telegramLink('https://t.me/+ExampleInvite/3034','3033'),null);
  for(const bad of ['https://evil.test/1702','https://t.me/cpa_seti/1703','javascript:alert(1)','https://t.me@evil.test/cpa_seti/1702']) assert.equal(telegramLink(bad,'1702'),null);
});
test('normalization keeps source date label without inventing timezone or confirmed affiliation',()=>{
  const rows=[{channelID:'123',postID:'4',channelTitle:'Channel',text:'Actual post',dateLabel:'2026-09-30 14:25:56',sourceURL:'https://t.me/channel/4'}];
  const posts=normalizeRows(rows,input.queries[0],'2026-10-11T18:00:00Z');
  assert.equal(posts[0].publishedAt,undefined);assert.equal(posts[0].sourceDateLabel,rows[0].dateLabel);
  assert.equal(posts[0].relevance,'unreviewed');assert.equal(posts[0].origin,'telemetrWebsite');
  assert.throws(()=>normalizeRows([...rows,...rows],input.queries[0]));
  assert.equal(pageFingerprint(rows),pageFingerprint([...rows].reverse()));
});
test('native export never promotes page limit or interrupted run to complete',()=>{
  const job=newJob(input),entry=newEntry(job);entry.totalCount=42;entry.done=true;entry.stopReason='pageLimit';
  let run=asNativeRun(job,entry);assert.equal(run.status,'partial');assert.equal(run.limitsBefore,undefined);assert.equal(run.queries[0].totalCount,42);
  entry.stopReason='end';run=asNativeRun(job,entry);assert.equal(run.status,'complete');assert.equal(run.origin,'telemetrWebsite');
});
