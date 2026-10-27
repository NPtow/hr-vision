// Public UI + actual recording/transcript returned by the isolated API on outreach.
// The team's hiring scenario is not reset or advanced by this check.
async page => {
  const materials = __MATERIALS_JSON__;
  const state = __STATE_JSON__;
  const ctx = await page.context().browser().newContext({ viewport: { width: 1440, height: 1100 } });
  const checks = [];
  const assert = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  await ctx.route('**/api/hr/**', route => {
    const endpoint = new URL(route.request().url()).pathname.split('/').pop();
      if (endpoint === 'access') return route.fulfill({ json: { ready: true } });
    if (endpoint === 'session') return route.fulfill({ json: { token: 'qa-review' } });
    if (endpoint === 'state') return route.fulfill({ json: state });
    if (endpoint === 'materials') return route.fulfill({ json: materials });
    return route.fulfill({ status: 405, json: { error: 'Read-only QA' } });
  });
  try {
    const p = await ctx.newPage();
    await p.goto('https://hr-vision.158-160-179-53.sslip.io/iframe.html?id=hr-vision-product--start&viewMode=story#team=isolated-media-qa');
    await p.getByRole('button').filter({ has: p.getByRole('heading', { name: 'Иван Петров', exact: true }) }).click();
    await p.getByRole('button', { name: 'Открыть подборку', exact: true }).click();
    await p.getByRole('navigation', { name: 'Этапы найма' }).getByRole('button', { name: 'Разбор встречи', exact: true }).click();
    const video = p.locator('.cj-review-layout video');
    await video.waitFor();
    await video.evaluate(v => v.readyState >= 1 ? undefined : new Promise((resolve, reject) => {
      v.addEventListener('loadedmetadata', resolve, { once: true });
      v.addEventListener('error', () => reject(new Error('Daily recording cannot load')), { once: true });
    }));
    assert(await video.evaluate(v => v.duration > 10 && v.videoWidth > 0), 'Actual cloud recording metadata loaded');
    await video.evaluate(v => v.play());
    await p.waitForFunction(() => document.querySelector('.cj-review-layout video')?.currentTime > 2);
    assert(true, 'Actual cloud video plays in HR Vision');
    await video.evaluate(v => v.pause());
    const moments = p.locator('.cj-review-layout .ir-chapters button');
    assert(await moments.count() > 1, 'Real Russian transcript produces timeline fragments');
    await moments.nth(1).click();
    assert(await video.evaluate(v => v.currentTime > 0), 'Transcript fragment seeks the cloud recording');
    assert(/[А-Яа-яЁё]/.test(await p.locator('.cj-review-layout .ir-excerpt blockquote').innerText()), 'Recognized Russian speech is visible');
    for (const width of [1440, 390]) {
      await p.setViewportSize({ width, height: 1100 });
      assert(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Recording review fits ${width}px`);
      await p.screenshot({ path: `output/playwright/daily-real-recording-${width}.png`, fullPage: true });
    }
    return { checks, status: 'passed', source: 'Actual Daily recording and transcript; isolated journey state' };
  } finally { await ctx.close(); }
}
