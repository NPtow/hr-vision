// Run through playwright-cli run-code after opening the XPM overview.
// Covers rendered geometry and real interactions, not a duplicate of layout math.
async (page) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const cases = [
    { width: 390, zoom: 1 }, { width: 768, zoom: 1 },
    { width: 1024, zoom: 1 }, { width: 1440, zoom: 1 },
    { width: 1440, zoom: 1.25 }, { width: 1440, zoom: 2 },
  ];
  const report = { scenes: 0, openedPoints: 0, labels: 0, failures: [] };
  for (const size of cases) {
    await page.setViewportSize({ width: size.width, height: 1050 });
    await page.evaluate(zoom => { document.documentElement.style.zoom = String(zoom); }, size.zoom);
    for (const strategy of ['Агентство', 'Контакты', 'Бесплатная ATS']) {
      await page.getByRole('button', { name: strategy, exact: true }).click();
      const chapters = page.getByRole('navigation', { name: 'Значимые ситуации' }).getByRole('button');
      if (await chapters.count() !== 6) throw new Error('Expected six chapters');
      for (let chapter = 0; chapter < 6; chapter++) {
        await chapters.nth(chapter).click();
        if (await page.locator('.xpm-detail').count()) throw new Error('Stale point detail');
        if (strategy === 'Агентство' && chapter === 1
          && await page.locator('.xpm-edge-label').filter({ hasText: /^Предложение$/ }).count() !== 1) {
          throw new Error('The reported caption regression is missing its readable text layer');
        }
        const inspection = await page.evaluate(() => {
          const bounds = element => {
            const r = element.getBoundingClientRect();
            return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
          };
          const intersect = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1
            && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
          const graph = bounds(document.querySelector('.xpm-fragment'));
          const labels = [...document.querySelectorAll('.xpm-edge-label')];
          const points = [...document.querySelectorAll('.xpm-point-name, .xpm-point-note, .xpm-point-symbol')];
          const problems = [];
          for (const point of points) {
            const r = bounds(point);
            if (r.left < graph.left - 1 || r.right > graph.right + 1 || r.top < graph.top - 1 || r.bottom > graph.bottom + 1) problems.push(`Point clipped: ${point.textContent || 'point symbol'}`);
          }
          for (const [index, label] of labels.entries()) {
            const r = bounds(label);
            const style = getComputedStyle(label);
            if (r.left < graph.left - 1 || r.right > graph.right + 1 || r.top < graph.top - 1 || r.bottom > graph.bottom + 1) problems.push(`Clipped: ${label.textContent}`);
            if (style.backgroundColor === 'rgba(0, 0, 0, 0)' || Number(style.opacity) < 1) problems.push(`Transparent backing: ${label.textContent}`);
            for (const point of points) if (intersect(r, bounds(point))) problems.push(`Overlap: ${label.textContent} / ${point.textContent || 'point symbol'}`);
            for (const other of labels.slice(index + 1)) if (intersect(r, bounds(other))) problems.push(`Labels overlap: ${label.textContent} / ${other.textContent}`);
          }
          if (document.documentElement.scrollWidth > innerWidth + 1) problems.push('Page overflows viewport');
          return { labels: labels.length, problems };
        });
        report.scenes++;
        report.labels += inspection.labels;
        for (const problem of inspection.problems) report.failures.push({ ...size, strategy, chapter: chapter + 1, problem });
        // Every point at desktop; one per scene at other widths/scales.
        const points = page.locator('.xpm-point');
        const count = size.width === 1440 && size.zoom === 1 ? await points.count() : 1;
        for (let index = 0; index < count; index++) {
          await points.nth(index).click();
          const detail = page.locator('.xpm-detail');
          if (!(await detail.isVisible()) || !(await detail.textContent()).includes('Вход')) throw new Error('Point content missing');
          const translucent = await page.locator('.xpm-edge-label').evaluateAll(labels => labels.some(label => Number(getComputedStyle(label).opacity) < 1));
          if (translucent) throw new Error('Selecting a point makes caption backing transparent');
          await page.getByRole('button', { name: 'Закрыть содержание точки' }).click();
          report.openedPoints++;
        }
      }
    }
  }
  await page.evaluate(() => { document.documentElement.style.zoom = ''; });
  await page.setViewportSize({ width: 1440, height: 1050 });
  const result = { ...report, errors, passed: report.failures.length === 0 && errors.length === 0 };
  if (!result.passed) throw new Error(JSON.stringify(result));
  return result;
}
