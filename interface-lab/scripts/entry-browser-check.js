// Public HTTPS only. No scenario mutations, resets, calls or messages.
// Materialize the invitation placeholder into an ignored, private temporary file.
async page => {
  const key = __PRIVATE_INVITATION__;
  const site = 'https://hr-vision.158-160-179-53.sslip.io';
  const start = site + '/iframe.html?id=hr-vision-product--start&viewMode=story';
  const browser = page.context().browser();
  const checks = [], errors = [], contexts = [];
  const check = (condition, name) => { if (!condition) throw new Error(name); checks.push(name); };
  page.on('pageerror', e => errors.push(e.message));
  const ready = async p => { await p.locator('.cj-account-card').first().waitFor(); check(await p.locator('.cj-account-card').count() === 4, 'Four ready accounts'); };
  const readState = async p => p.evaluate(async () => (await fetch('/api/hr/state', { headers: { Authorization: 'Bearer ' + sessionStorage.getItem('hr-vision-connected-session') } })).json());
  const context = async options => { const c = await browser.newContext(options); contexts.push(c); return c; };
  try {
    await ready(page);
    let managerToken, before;
    for (const [actor, name] of [['manager', 'Иван Петров'], ['anna', 'Анна Миронова'], ['mikhail', 'Михаил Белов'], ['elena', 'Елена Орлова']]) {
      await page.getByRole('button').filter({ has: page.getByRole('heading', { name, exact: true }) }).click();
      await page.locator('.cj-account').waitFor();
      check((await readState(page)).actor === actor, 'Server session for ' + actor);
      if (actor === 'manager') {
        before = await readState(page);
        managerToken = await page.evaluate(() => sessionStorage.getItem('hr-vision-connected-session'));
      }
      await page.getByRole('button', { name: 'HR Vision.', exact: true }).click();
      await ready(page);
    }
    await page.reload(); await ready(page);
    check(!new URL(page.url()).hash && await page.locator('input').count() === 0, 'Clean URL reload needs no code');
    const next = await page.context().newPage();
    await next.goto(start); await ready(next); await next.close();
    check(true, 'New tab works without invitation parameter');

    const restored = await context({ storageState: await page.context().storageState() });
    const restoredPage = await restored.newPage();
    await restoredPage.goto(start); await ready(restoredPage);
    check(true, 'Persisted browser access works without sessionStorage');

    const fresh = await context({ viewport: { width: 390, height: 844 } });
    const freshPage = await fresh.newPage();
    await freshPage.goto(start + '#team=' + encodeURIComponent(key)); await ready(freshPage);
    check(!new URL(freshPage.url()).hash, 'First visit consumes full invitation');
    await freshPage.screenshot({ path: 'output/playwright/entry-ready-390.png', fullPage: true });
    check(await freshPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '390px layout has no horizontal overflow');

    const legacy = await context();
    await legacy.addInitScript(key => sessionStorage.setItem('hr-vision-team', key), key);
    const legacyPage = await legacy.newPage(); await legacyPage.goto(start); await ready(legacyPage);
    check(await legacyPage.evaluate(() => !sessionStorage.getItem('hr-vision-team')), 'Old stored invitation migrates and is removed');

    const session = await context();
    await session.addInitScript(token => sessionStorage.setItem('hr-vision-connected-session', token), managerToken);
    const sessionPage = await session.newPage(); await sessionPage.goto(start); await ready(sessionPage);
    check((await session.cookies()).some(c => c.name === '__Host-hr-vision-access'), 'Existing account session restores browser access');

    const invalid = await context();
    const invalidPage = await invalid.newPage();
    await invalidPage.goto(start + '#team=invalid');
    await invalidPage.getByRole('heading', { name: 'Откройте актуальную ссылку HR Vision из чата' }).waitFor();
    check(await invalidPage.locator('input,.cj-account-card').count() === 0, 'Invalid invitation shows guidance without a code field');

    const invite = await page.request.post(site + '/api/hr/invite', { data: {}, headers: { Authorization: 'Bearer ' + managerToken } });
    check(invite.ok() && (await invite.json()).url === start + '#team=' + key, 'Authorized invitation includes working access');
    const after = await (await page.request.get(site + '/api/hr/state', { headers: { Authorization: 'Bearer ' + managerToken } })).json();
    check(before.revision === after.revision && before.generation === after.generation, 'Shared scenario unchanged');
    check(errors.length === 0, 'No JavaScript errors');
    return { checks, count: checks.length, revision: after.revision, errors };
  } finally {
    for (const c of contexts) await c.close();
  }
}
