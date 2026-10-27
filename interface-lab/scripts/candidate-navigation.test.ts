import assert from 'node:assert/strict';
import { test } from 'node:test';
import { candidateLoginTarget, candidateNavigationURL, candidatePage } from '../src/scenes/candidate-portal/candidate-navigation.ts';

for (const cp of ['home', 'interview', 'meeting'] as const) {
  test(`notification ${cp} survives login and reload with exact application/meeting`, () => {
    const entry = `https://fixture.test/iframe.html?id=hr-vision-product--candidate&cp=${cp}&application=owned&meeting=exact`;
    const login = candidateNavigationURL(entry, 'login', undefined, undefined, true);
    assert.equal(candidatePage(login.searchParams), 'login');
    // Reparse persisted URL as a newly mounted page after reload does.
    const reload = new URL(login.href);
    const target = candidateLoginTarget(reload.searchParams);
    const resumed = candidateNavigationURL(reload.href, target, reload.searchParams.get('application')!, reload.searchParams.get('meeting')!);
    assert.equal(candidatePage(resumed.searchParams), cp);
    assert.equal(resumed.searchParams.get('application'), 'owned');
    assert.equal(resumed.searchParams.get('meeting'), 'exact');
    assert.equal(resumed.searchParams.has('returnCp'), false);
    assert.equal(resumed.origin, 'https://fixture.test');
  });
}
test('unknown/external/capability/login return targets fall back to internal home', () => {
  for (const value of ['https://evil.test/', '//evil.test', 'guest', 'claim', 'invite-booking', 'login', 'unknown', 'register']) {
    const query = new URLSearchParams({ cp: 'login', returnCp: value });
    assert.equal(candidateLoginTarget(query), 'home');
  }
});
test('explicit logout and ordinary navigation clear previous return context', () => {
  const url = candidateNavigationURL('https://fixture.test/iframe.html?cp=meeting&returnCp=meeting&application=a&meeting=m', 'login');
  for (const key of ['returnCp', 'application', 'meeting']) assert.equal(url.searchParams.has(key), false);
});
