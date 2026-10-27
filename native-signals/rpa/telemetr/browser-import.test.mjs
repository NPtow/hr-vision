import test from 'node:test';
import assert from 'node:assert/strict';
import {browserRun} from './browser-import.mjs';
import {resultCounter} from './contract.mjs';
const query = {companyID:'a',companyName:'A',query:'A'};
const manifest={id:'rpa-browser-test',startedAt:'2026-10-11T12:00:00Z',dateFrom:'2026-09-11',dateTo:'2026-10-11',queries:[query]};
const row={channelID:'123',postID:'4',channelTitle:'A',text:'Text',deleted:true,retrievedAt:'2026-10-11T12:00:00Z'};
const page={companyID:'a',query:'A',page:1,url:'https://telemetr.me/posts?text=A&network=tg&date_from=2026-09-11&date_to=2026-10-11',rows:[row],capturedRows:[row],links:[],totalCount:1,counter:'Найден 1 пост'};
test('deleted archive has no invented link and complete only at known end',()=>{
 const {run}=browserRun(manifest,[page]);
 assert.equal(run.status,'complete'); assert.equal(run.queries[0].posts[0].sourceURL,undefined);
 assert.equal(run.queries[0].posts[0].sourceDeleted,true);
 assert.equal(browserRun(manifest,[{...page,totalCount:2,counter:'Найдено 2 поста'}]).run.status,'partial');
 assert.equal(browserRun(manifest,[{...page,approximate:true}]).run.status,'partial');
});
test('rejects mixed pages, invented links and incomplete card identity',()=>{
 assert.throws(()=>browserRun(manifest,[{...page,url:page.url.replace('text=A','text=B')}]),/mismatch/);
 assert.throws(()=>browserRun(manifest,[{...page,capturedRows:[{...row,deleted:false}]}]),/Missing original/);
 assert.throws(()=>browserRun(manifest,[{...page,capturedRows:[{...row,postID:'5'}]}]),/mismatched/);
});
test('another pending company keeps run partial',()=>{
 assert.equal(browserRun({...manifest,queries:[query,{...query,companyID:'b'}]},[page]).run.status,'partial');
});
test('table fragments preserve provenance and do not invent card completeness',()=>{
 const fragment={channelID:'123',postID:'4',channelTitle:'A',text:'…fragment'};
 const p={...page,rows:[fragment],capturedRows:[],captureMode:'table',retrievedAt:row.retrievedAt};
 const result=browserRun(manifest,[p]).run;
 assert.equal(result.status,'complete');
 assert.equal(result.posts,undefined);
 const post=result.queries[0].posts[0];
 assert.equal(post.textFromCard,false);
 assert.equal(post.sourceDeleted,undefined);
 assert.equal(post.sourceURL,undefined);
 assert.equal(post.sourcePageURL,page.url);
 assert.equal(browserRun({...manifest,finished:true},[{...p,totalCount:222,counter:'Найдено 222 поста'}]).progress.state,'partial');
 const upgraded=browserRun(manifest,[{...p,capturedRows:[row]}]).run.queries[0].posts[0];
 assert.equal(upgraded.textFromCard,true);
 assert.equal(upgraded.text,'Text');
});
test('exact phrase must not silently become all-word search',()=>{
 assert.throws(()=>browserRun({...manifest,queries:[{...query,searchType:'exact'}]},[page]),/mismatch/);
});
test('Russian counters preserve singular and thousands, blank is not zero',()=>{
 assert.equal(resultCounter("Найдено 1'798 постов"),1798);
 assert.equal(resultCounter('Найден 21 пост'),21);
 assert.equal(resultCounter('Найдено 1 798 постов'),1798);
 assert.equal(resultCounter(''),null);
 assert.equal(resultCounter('','Ничего не найдено'),0);
 assert.throws(()=>browserRun(manifest,[{...page,totalCount:1,counter:"Найдено 1'798 постов"}]),/counter does not match/);
});
test('moving search pages deduplicate without claiming missing records complete',()=>{
 const p1={...page,totalCount:2,counter:'Найдено 2 поста',links:[{url:page.url+'&page=2'}]};
 const p2={...p1,page:2,url:page.url+'&page=2',links:[]};
 const run=browserRun(manifest,[p1,p2]).run;
 assert.equal(run.status,'partial');
 assert.equal(run.queries[0].posts.length,1);
 assert.equal(JSON.parse(run.queries[0].rawResponseJSON).pages[1].duplicates,1);
});
