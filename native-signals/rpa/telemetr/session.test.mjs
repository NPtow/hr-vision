import test from 'node:test';
import assert from 'node:assert/strict';
import {telemetrSessionCookies} from './session.mjs';
test('restored login is limited to Telemetr, unexpired cookies and HTTPS',()=>{
  const cookie={name:'fixture',value:'test-only',domain:'.telemetr.me',path:'/',expires:-1,httpOnly:true,secure:false,sameSite:'Lax'};
  const clean=telemetrSessionCookies([cookie,{...cookie,domain:'.yandex.ru'},{...cookie,domain:'telemetr.me.evil.test'},{...cookie,expires:1}]);
  assert.equal(clean.length,1);assert.equal(clean[0].secure,true);assert.equal(clean[0].expires,-1);
  assert.deepEqual(telemetrSessionCookies([]),[]);assert.throws(()=>telemetrSessionCookies({}));
});
