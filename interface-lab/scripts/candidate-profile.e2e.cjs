// Materialize __LOCAL_STAGE_FIXTURE__ from test_employer_stage_acceptance.py
// --serve-fixture into an ignored local output file, then playwright-cli run-code.
// Real temporary HTTP/SQLite state, no live account. Original-file rendering uses
// one explicit local document response; source facts and history use the backend.
async page => {
  const config = __LOCAL_STAGE_FIXTURE__;
  const uiOrigin = config.uiOrigin || 'http://127.0.0.1:6018';
  const local = value => ['127.0.0.1', 'localhost', '[::1]'].includes(new URL(value).hostname);
  if (!config.localOnly || !local(config.apiBase) || !local(uiOrigin)) throw Error('Local fixture required');
  const checks = [], errors = [], mutations = [], resumeRequests = [], screenshots = [];
  let originalFile = false;
  const originalText = 'Исходное резюме\nСодержимое файла без переформатирования.';
  const assert = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const button = name => page.getByRole('button', { name, exact: true });
  const profile = () => page.getByRole('region', { name: 'Профиль', exact: true });
  const read = async () => {
    const response = await page.request.get(new URL(`employer/state?vacancyId=${config.vacancy.id}`, config.apiBase).href,
      { headers: { authorization: `Bearer ${config.token}` } });
    if (!response.ok()) throw Error(`Local state ${response.status()}`);
    return response.json();
  };
  const decorate = state => {
    if (originalFile) state.candidates[config.applicationId].sourceProfile.resume = {
      filename: 'original-resume.txt', mime: 'text/plain', size: originalText.length, uploadedAt: '2026-10-20T00:00:00Z',
    };
    return state;
  };
  const routeHandler = async route => {
    const request = route.request(), url = new URL(request.url());
    const relative = url.pathname.replace(/^\/api\/hr\//, '') + url.search;
    if (relative.startsWith('employer/session')) return route.fulfill({ json: { token: config.token, state: decorate(await read()) } });
    if (relative === 'employer/action') mutations.push(request.postDataJSON());
    if (relative === 'employer/screening/resume') resumeRequests.push(request.postDataJSON());
    if (originalFile && relative === 'employer/screening/resume') {
      return route.fulfill({ json: { filename: 'original-resume.txt', mime: 'text/plain',
        contentBase64: Buffer.from(originalText).toString('base64') } });
    }
    const response = await route.fetch({ url: new URL(relative, config.apiBase).href,
      headers: { authorization: `Bearer ${config.token}`, 'content-type': 'application/json' } });
    if (relative.startsWith('employer/state?') && response.ok()) return route.fulfill({ response, json: decorate(await response.json()) });
    return route.fulfill({ response });
  };
  const onError = error => errors.push(error.message);
  page.on('pageerror', onError);
  await page.route('**/api/hr/**', routeHandler);
  const url = new URL('/iframe.html', uiOrigin);
  Object.entries({ id: 'hr-vision-product--manager', viewMode: 'story', screen: 'shortlist',
    pool: 'selected', vacancyId: config.vacancy.id, applicationId: config.applicationId,
    stageId: `interview:${config.initialInterviewId}` }).forEach(([key, value]) => url.searchParams.set(key, value));
  const exactIdentity = () => {
    const current = new URL(page.url());
    assert(current.searchParams.get('vacancyId') === config.vacancy.id && current.searchParams.get('applicationId') === config.applicationId,
      'Profile navigation retains vacancy and application identity');
  };
  const shot = async name => {
    const path = `output/playwright/candidate-profile-20261020/${name}.png`;
    await page.screenshot({ path, fullPage: true }); screenshots.push(path);
  };
  try {
    const baseline = (await read()).candidates[config.applicationId];
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(url.href);
    await page.locator('.esc-interview').waitFor();
    assert(await page.getByRole('tablist', { name: 'Этапы кандидата' }).getByRole('tab').count() === 3,
      'Active history retains initial interview and both meetings');
    await button('Добавить следующий этап').click();
    assert(JSON.stringify(await page.getByRole('menuitem').allTextContents()) === JSON.stringify(['Встреча', 'Оффер']),
      'Next-stage menu only offers current supported transitions');
    await page.keyboard.press('Escape');
    const name = button(`Профиль: ${config.candidateName}`);
    await name.focus(); await page.keyboard.press('Enter');
    await profile().waitFor(); exactIdentity();
    assert(new URL(page.url()).searchParams.get('stageId') === `interview:${config.initialInterviewId}`,
      'Opening profile preserves the previously viewed stage');
    assert(new URL(page.url()).searchParams.get('view') === 'profile' && await name.getAttribute('aria-expanded') === 'true',
      'Profile is an explicit URL view and accessible selected state');
    const source = baseline.sourceProfile;
    for (const fact of source.profile) {
      if (fact.value?.trim()) assert((await profile().innerText()).includes(fact.value), `Profile retains sourced ${fact.label}`);
    }
    await profile().getByRole('button', { name: 'Открыть', exact: true }).click();
    assert(await profile().locator('.ecp-resume-source-text').innerText() === source.cv,
      'Resume preview uses authentic stored source text');
    const downloaded = page.waitForEvent('download');
    await button('Скачать резюме').click();
    const download = await downloaded;
    assert(download.suggestedFilename() === source.resume.filename && await download.failure() === null,
      'Original resume downloads with the stored filename');
    await shot('profile-desktop');
    await page.reload(); await profile().waitFor(); exactIdentity();
    await button('К этапам').click(); await page.locator('.esc-interview').waitFor();
    assert(new URL(page.url()).searchParams.get('stageId') === `interview:${config.initialInterviewId}`,
      'Return from reloaded profile restores the previous interview');
    await page.goBack(); await profile().waitFor();
    await page.goForward(); await page.locator('.esc-interview').waitFor();
    assert(true, 'Browser Back and Forward restore profile and exact stage');
    const firstMeeting = page.getByRole('tablist', { name: 'Этапы кандидата' }).getByRole('tab').nth(1);
    await firstMeeting.click();
    await page.locator(`[data-stage-id="meeting:${config.firstMeetingId}"]`).waitFor();
    await name.click(); await profile().waitFor();
    await button('К этапам').click();
    await page.locator(`[data-stage-id="meeting:${config.firstMeetingId}"]`).waitFor();
    assert(true, 'Profile returns to historical meeting rather than latest meeting');
    const archived = new URL(url.href); archived.searchParams.set('stageId', `test:${config.firstTestId}`);
    await page.goto(archived.href);
    await page.locator(`[data-stage-id="meeting:${config.secondMeetingId}"]`).waitFor();
    assert(new URL(page.url()).searchParams.get('stageId') === `meeting:${config.secondMeetingId}`,
      'Owned archived test link canonically falls back to last active stage');
    archived.searchParams.set('stageId', 'test:foreign-or-unknown'); await page.goto(archived.href);
    await page.getByRole('heading', { name: 'Этап недоступен', exact: true }).waitFor();
    assert(new URL(page.url()).searchParams.get('stageId') === 'test:foreign-or-unknown',
      'Unknown test identity stays unavailable, never silently substitutes history');
    originalFile = true;
    const original = new URL(url.href); original.searchParams.set('view', 'profile');
    await page.goto(original.href); await profile().waitFor();
    await profile().getByRole('button', { name: 'Открыть', exact: true }).click();
    await profile().getByText(originalText, { exact: true }).waitFor();
    assert(resumeRequests.length >= 3 && resumeRequests.every(request => request.applicationId === config.applicationId && request.vacancyId === config.vacancy.id),
      'Original resume request keeps exact application and vacancy access scope');
    assert(await profile().locator('.ecp-resume-source-text').innerText() === originalText,
      'Stored original file takes precedence over extracted CV text');
    await page.setViewportSize({ width: 390, height: 844 });
    const width = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
    assert(width.content <= width.viewport + 1, 'Profile including original resume fits 390px without horizontal overflow');
    await shot('profile-mobile');
    assert(mutations.length === 0, 'Profile and stage browsing perform no workflow mutations');
    assert(JSON.stringify((await read()).candidates[config.applicationId].stages) === JSON.stringify(baseline.stages),
      'History remains unchanged after profile, Back/Forward and archived-link navigation');
    assert(errors.length === 0, 'No browser JavaScript exceptions');
    return { status: 'passed', boundary: 'local HTTP and SQLite; original file response is a local UI fixture', checks, screenshots, errors };
  } catch (cause) {
    await shot('profile-failure');
    return { status: 'failed', failure: cause.message, checks, screenshots, errors };
  } finally { await page.unroute('**/api/hr/**', routeHandler); page.off('pageerror', onError); }
}
