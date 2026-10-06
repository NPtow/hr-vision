// Playwright CLI run-code, on the standalone gallery story.
async (page) => {
  const failures = [], checks = [];
  page.on('pageerror', error => failures.push(error.message));
  const assert = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
  await page.getByRole('heading', { name: 'Как смотреть подборку', exact: true }).waitFor();
  const choices = page.getByRole('group', { name: 'Варианты интерфейса' }).getByRole('button');
  assert(await choices.count() === 10, 'Ten selectable alternatives');
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (let i = 0; i < 10; i++) {
      await choices.nth(i).click();
      assert(await choices.nth(i).getAttribute('aria-pressed') === 'true', `Variant ${i+1} selected at ${width}`);
      assert(await page.locator('.hpa-view').isVisible(), `Variant ${i+1} content at ${width}`);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Variant ${i+1} fits ${width}`);
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await choices.nth(0).click();
  await page.locator('.hpa-person-row').nth(1).click();
  assert((await page.locator('.hpa-split-detail').innerText()).includes('Михаил Белов'), 'Split profile changes with selection');
  await page.locator('.hpa-split-detail .hpa-evidence').first().click();
  assert(await page.getByRole('dialog').isVisible(), 'Evidence dialog opens');
  assert((await page.getByRole('dialog').innerText()).includes('Демонстрационный текст'), 'Evidence source boundary visible');
  await page.keyboard.press('Escape');
  assert(await page.getByRole('dialog').count() === 0, 'Evidence Escape dismissal');
  await choices.nth(1).click();
  await page.getByRole('button', { name: 'Открыть профиль: Елена Орлова', exact: true }).click();
  assert(await page.locator('.hpa-sheet').isVisible(), 'Table opens detail sheet');
  await page.keyboard.press('Escape');
  assert(await page.getByRole('dialog').count() === 0, 'Sheet Escape dismissal');
  // Escape immediately after a portal opens must dismiss only the top view.
  for (let attempt = 1; attempt <= 3; attempt++) {
    await page.getByRole('button', { name: 'Открыть профиль: Анна Миронова', exact: true }).click();
    await page.locator('.hpa-sheet .hpa-evidence').first().click();
    await page.locator('.hpa-fragment').waitFor();
    await page.keyboard.press('Escape');
    await page.locator('.hpa-fragment').waitFor({ state: 'detached' });
    assert(await page.locator('.hpa-sheet').isVisible(), `Nested evidence preserves profile ${attempt}`);
    await page.waitForFunction(() => document.activeElement?.classList.contains('hpa-evidence'));
    assert(await page.evaluate(() => document.activeElement?.classList.contains('hpa-evidence')), `Nested evidence restores focus ${attempt}`);
    await page.keyboard.press('Escape');
    await page.locator('.hpa-sheet').waitFor({ state: 'detached' });
    await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === 'Открыть профиль: Анна Миронова');
    assert(await page.evaluate(() => document.activeElement?.getAttribute('aria-label') === 'Открыть профиль: Анна Миронова'), `Profile restores table focus ${attempt}`);
  }
  await choices.nth(2).click();
  const firstAccordion = page.locator('.hpa-accordion button[data-state]').first();
  await firstAccordion.click();
  assert(await firstAccordion.getAttribute('data-state') === 'closed', 'Accordion can close the only open profile');
  await firstAccordion.click();
  assert(await firstAccordion.getAttribute('data-state') === 'open', 'Accordion reopens the profile');
  await choices.nth(4).click();
  await page.getByRole('button', { name: 'Следующий кандидат', exact: true }).first().click();
  assert((await page.locator('.hpa-focus .hpa-profile').innerText()).includes('Михаил Белов'), 'Focused view advances to next candidate');
  await choices.nth(5).click();
  await page.getByRole('group', { name: 'Кандидат', exact: true }).getByRole('button').nth(2).click();
  assert((await page.locator('.hpa-document-title').innerText()).includes('Елена Орлова'), 'Dossier changes candidate');
  await choices.nth(9).click();
  await page.getByRole('tab', { name: 'Что уточнить', exact: true }).click();
  assert(await page.locator('.hpa-lens-unknown').count() === 3, 'Task lens shows unknowns for all candidates');
  await page.getByRole('tab', { name: 'Условия', exact: true }).click();
  assert(await page.locator('.hpa-lens .hpa-facts').count() === 3, 'Task lens shows conditions for all candidates');
  await page.setViewportSize({ width: 390, height: 1000 });
  await choices.nth(7).click();
  await page.locator('.hpa-reading-index button').nth(2).click();
  assert(await page.locator('.hpa-reading-open .hpa-reading-page').isVisible(), 'Mobile evidence opens reading page');
  await page.getByRole('button', { name: 'К кейсам', exact: true }).click();
  assert(await page.locator('.hpa-reading-index').isVisible(), 'Mobile evidence returns to index');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await choices.nth(8).click();
  await page.getByRole('button', { name: 'Следующий кандидат', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.hpa-strip').scrollLeft > 0);
  assert(await page.locator('.hpa-strip-card[data-person="mikhail"]').getAttribute('class').then(value => value.includes('hpa-active-card')), 'Profile strip advances with reduced motion');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await choices.nth(3).click();
  await page.getByRole('button', { name: 'Интервью и профиль', exact: true }).nth(2).click();
  assert((await page.locator('.hpa-card-page').innerText()).includes('Елена Орлова'), 'Card opens full profile');
  await page.getByRole('button', { name: 'К трём кандидатам', exact: true }).click();
  assert(await page.locator('.hpa-person-card').count() === 3, 'Card view returns to shortlist');
  await page.getByRole('button', { name: 'Задачи найма', exact: true }).click();
  assert(await page.getByRole('heading', { name: 'Задачи найма', exact: true }).isVisible(), 'Task context reachable');
  await page.locator('.hpa-task').click();
  assert(await page.locator('.hpa-view').isVisible(), 'Return from task to gallery shortlist');
  const validUrl = new URL(page.url());
  validUrl.searchParams.set('variant', '1.5');
  await page.goto(validUrl.href);
  await page.getByRole('heading', { name: 'Как смотреть подборку', exact: true }).waitFor();
  assert(await page.getByRole('button', { name: 'Вариант 01: Список и профиль', exact: true }).getAttribute('aria-pressed') === 'true', 'Invalid fractional variant recovers to first');
  assert(failures.length === 0, `No page errors: ${failures.join('; ')}`);
  return { passed: checks.length, checks, errors: failures };
}
