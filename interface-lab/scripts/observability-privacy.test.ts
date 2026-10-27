import { test } from 'node:test';
import assert from 'node:assert/strict';
import { safeUrl, safeAttributes, sanitizeReplay } from '../src/observability/privacy.ts';
const origin = 'https://mashadasha.tech';
test('URLs and browser errors do not export credentials or error messages', () => {
  const attrs = safeAttributes({'http.url':origin+'/api/hr/candidate/SECRET/resume?token=SECRET', 'exception.message':'PRIVATE CV', 'http.request.body':'PRIVATE CV', 'exception.stacktrace':'Error: PRIVATE CV\n    at f (https://mashadasha.tech/assets/app.js?token=SECRET:2:4)', 'http.status_code':500}, origin);
  const json = JSON.stringify(attrs);
  assert.ok(!json.includes('SECRET') && !json.includes('PRIVATE'));
  assert.equal(attrs['http.status_code'],500);
  assert.equal(safeUrl(origin+'/iframe.html?token=SECRET#secret',origin), origin+'/iframe.html');
});
test('both full and incremental rrweb payloads redact text and DOM attributes', () => {
  const event = {type:3,data:{href:origin+'/iframe.html?token=SECRET',adds:[{node:{tagName:'input',attributes:{value:'SECRET',title:'SECRET','data-user':'SECRET',placeholder:'SECRET',class:'field'},textContent:'SECRET'}}],texts:[{id:1,value:'SECRET'}],text:'SECRET',attributes:[{id:1,attributes:{href:origin+'/resume/SECRET',title:'SECRET',style:'background:url(SECRET);width:100px'}}]}};
  const safe = sanitizeReplay(event,origin);
  assert.ok(!JSON.stringify(safe).includes('SECRET'));
  assert.equal(safe.data.adds[0].node.attributes.class,'field');
});
