import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isEmployerMiniApp, restoreEmployerIdentity, type TelegramApp } from '../src/lib/telegram-employer.ts';
const app = (initData: string): TelegramApp => ({ initData, ready() {}, expand() {} });

test('ordinary browser uses its normal employer session', async () => {
  let exchanges = 0;
  assert.equal(await restoreEmployerIdentity(undefined, async () => 'browser', async () => { exchanges++; return 'telegram'; }), 'browser');
  assert.equal(await restoreEmployerIdentity(app(''), async () => 'browser', async () => { exchanges++; return 'telegram'; }), 'browser');
  assert.equal(exchanges, 0);
});
test('valid Mini App launch takes precedence over unrelated existing cookie session', async () => {
  let browsers = 0;
  assert.equal(await restoreEmployerIdentity(app('signed-data'), async () => { browsers++; return 'other-workspace'; }, async raw => { assert.equal(raw, 'signed-data'); return 'linked-workspace'; }), 'linked-workspace');
  assert.equal(browsers, 0);
});
test('invalid, expired and unlinked Telegram never restore another browser identity', async () => {
  let browsers = 0;
  for (const reason of ['invalid', 'expired', 'unlinked']) await assert.rejects(restoreEmployerIdentity(app('signed-data'), async () => { browsers++; return 'wrong'; }, async () => { throw new Error(reason); }), new RegExp(reason));
  assert.equal(browsers, 0);
});
test('SDK entry is explicit; ordinary and candidate URLs do not load it', () => {
  assert.equal(isEmployerMiniApp('?miniapp=employer&screen=shortlist&vacancyId=owned'), true);
  for (const query of ['', '?auth=login', '?miniapp=candidate', '?screen=shortlist']) assert.equal(isEmployerMiniApp(query), false);
});

test('missing initData in a native Telegram client cannot restore browser identity', async () => {
  let browsers = 0;
  await assert.rejects(restoreEmployerIdentity({ ...app(''), platform: 'android' }, async () => { browsers++; return 'wrong'; }, async () => 'unexpected'), /Нет данных/);
  assert.equal(browsers, 0);
});
