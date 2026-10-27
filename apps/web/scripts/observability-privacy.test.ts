import { test } from 'node:test';
import assert from 'node:assert/strict';
import { safeUrl, safeAttributes, sanitizeReplay, createTextMask, createReplaySanitizer, safeCss } from '../src/observability/privacy.ts';
import { extractStaticCopy } from './replay-static-copy.mjs';
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

test('native session timeline gets an error marker without raw error text', () => {
  const attrs = safeAttributes({component:'error','error.message':'PRIVATE CV','error.stack':'TypeError: PRIVATE CV\n    at f (https://mashadasha.tech/assets/app.js?secret=PRIVATE:1:2)'},origin);
  assert.equal(attrs.component,'error');
  assert.ok(!JSON.stringify(attrs).includes('PRIVATE'));
  assert.equal(safeAttributes({'error.message':'TypeError'},origin)['error.message'],'TypeError');
});

test('static UI labels stay readable while names, user text and field values stay masked', () => {
  const copy = extractStaticCopy(`const view = <><button>Войти</button><h1>{'Вход в кабинет'}</h1><span>{user.name}</span><label>Рабочая почта</label></>`);
  const text = createTextMask(copy);
  assert.equal(text('Войти'), 'Войти');
  assert.equal(text('  Вход\nв кабинет  '), '  Вход\nв кабинет  ');
  assert.equal(text('Private Candidate'), '******* *********');
  const sanitize = createReplaySanitizer(origin, text);
  const snapshot = sanitize({type:2,data:{node:{id:1,type:0,childNodes:[
    {id:2,type:2,tagName:'button',attributes:{},childNodes:[{id:3,type:3,textContent:'Войти'}]},
    {id:4,type:2,tagName:'input',attributes:{value:'Войти',placeholder:'Рабочая почта'},childNodes:[]},
    {id:5,type:2,tagName:'p',attributes:{},childNodes:[{id:6,type:3,textContent:'Private Candidate'}]},
  ]}}});
  assert.equal(snapshot.data.node.childNodes[0].childNodes[0].textContent, 'Войти');
  assert.equal(snapshot.data.node.childNodes[1].attributes.value, '*****');
  assert.equal(snapshot.data.node.childNodes[1].attributes.placeholder, 'Рабочая почта');
  assert.ok(!JSON.stringify(snapshot).includes('Private Candidate'));
  const update = sanitize({type:3,data:{source:0,texts:[{id:3,value:'Вход в кабинет'},{id:6,value:'Another Private Candidate'}]}});
  assert.equal(update.data.texts[0].value,'Вход в кабинет');
  assert.ok(!JSON.stringify(update).includes('Private'));
  assert.equal(sanitize({type:3,data:{source:5,id:4,text:'Войти'}}).data.text, '*****');
});

test('rrweb 2.1 style text and incremental stylesheet changes retain layout and public fonts', () => {
  const sanitize = createReplaySanitizer(origin, createTextMask(['Войти']));
  const css = '.form {display:flex;justify-content:center;align-content:start;font-family:Inter} @font-face {src:url(https://mashadasha.tech/assets/inter.woff2)}';
  const snapshot = sanitize({type:2,data:{node:{id:1,type:0,childNodes:[
    {id:2,type:2,tagName:'style',attributes:{},childNodes:[{id:3,type:3,textContent:css}]},
    {id:4,type:2,tagName:'link',attributes:{rel:'stylesheet',href:origin+'/assets/app.css'},childNodes:[]},
  ]}}});
  assert.ok(snapshot.data.node.childNodes[0].childNodes[0].textContent.includes('justify-content:center'));
  assert.ok(snapshot.data.node.childNodes[0].childNodes[0].textContent.includes('/assets/inter.woff2'));
  const update = sanitize({type:3,data:{source:0,texts:[{id:3,value:css}],attributes:[{id:4,attributes:{_cssText:css,href:origin+'/assets/app.css?token=SECRET'}}]}});
  assert.ok(update.data.texts[0].value.includes('display:flex'));
  assert.ok(update.data.attributes[0].attributes._cssText.includes('align-content:start'));
  assert.equal(update.data.attributes[0].attributes.href, origin+'/assets/app.css');
  assert.ok(!JSON.stringify(update).includes('SECRET'));
  assert.equal(safeCss('.x{content:"PRIVATE";justify-content:center;background:url(https://private.invalid/SECRET)}', origin),'.x{content:"";justify-content:center;background:none}');
});
