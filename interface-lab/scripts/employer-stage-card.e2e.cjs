// playwright-cli run-code --filename <materialized copy of this file>
// Start server/test_employer_stage_acceptance.py --serve-fixture /tmp/stages.json,
// replace __LOCAL_STAGE_FIXTURE__ with that JSON in an ignored output/ copy, and
// run against local Storybook (default :6023). Never inject production sessions.
// All mutations reach a real isolated HTTP handler/temporary SQLite database.
// Fake Daily is the provider boundary; screenshots are local UI evidence only.
async page => {
  const config = __LOCAL_STAGE_FIXTURE__;
  const local = url => ['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname);
  const uiOrigin = config.uiOrigin || 'http://127.0.0.1:6023';
  if (!config.localOnly || !local(config.apiBase) || !local(uiOrigin)) throw Error('Local fixture required');
  const checks = [], errors = [], actions = [], materials = [], screenshots = [];
  const onError = error => errors.push(error.message);
  page.on('pageerror', onError);
  const assert = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const body = (name, value) => page.getByRole(name, { name: value, exact: true });
  const button = name => body('button', name);
  const api = async (path, data) => {
    const response = await page.request.fetch(new URL(path, config.apiBase).href, {
      method: data ? 'POST' : 'GET', data,
      headers: { authorization: `Bearer ${config.token}`, 'content-type': 'application/json' },
    });
    const result = await response.json();
    if (!response.ok()) throw Error(`Local API ${path}: ${response.status()} ${JSON.stringify(result)}`);
    return result;
  };
  const read = (vid = config.vacancy.id) => api(`employer/state?vacancyId=${encodeURIComponent(vid)}`);
  const person = async () => (await read()).candidates[config.applicationId];
  const card = page.locator('.esc-card[data-candidate-id]');
  const tabs = () => page.getByRole('tablist', { name: 'Этапы кандидата', exact: true }).getByRole('tab');
  const panel = () => page.locator('.esc-stage-panel');
  const selectStage = async id => {
    const current = await person();
    const index = current.stages.findIndex(stage => stage.id === id);
    assert(index >= 0, `Stage ${id} exists in persisted chain`);
    await tabs().nth(index).click();
    await page.locator(`[data-stage-id="${id}"]`).waitFor({ state: 'visible' });
  };
  const exactCard = async (aid, vid) => {
    await page.locator(`.esc-card[data-candidate-id="${aid}"]`).waitFor({ state: 'visible' });
    const url = new URL(page.url());
    assert(url.searchParams.get('applicationId') === aid, 'URL keeps exact application identity');
    assert(url.searchParams.get('vacancyId') === vid, 'URL keeps exact vacancy identity');
  };
  const goto = async (screen, vid = config.vacancy.id, aid = config.applicationId) => {
    const url = new URL('/iframe.html', uiOrigin);
    Object.entries({ id: 'hr-vision-product--manager', viewMode: 'story', screen,
      vacancyId: vid, applicationId: aid, pool: 'selected' }).forEach(([key, value]) => url.searchParams.set(key, value));
    await page.goto(url.href);
  };
  const fit = async label => {
    const size = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
    assert(size.scroll <= size.width + 1, `${label}: no page-level horizontal overflow (${size.width}px)`);
  };
  const activeStageVisible = async label => {
    await page.waitForFunction(() => {
      const strip = document.querySelector('.esc-stage-scroll');
      const active = strip?.querySelector('[role="tab"][aria-selected="true"]');
      if (!strip || !active) return false;
      const bounds = strip.getBoundingClientRect(), selected = active.getBoundingClientRect();
      return selected.left >= bounds.left - 1 && selected.right <= bounds.right + 1;
    });
    assert(true, label);
  };
  const fillOffer = async values => {
    for (const [label, key] of [['Должность', 'role'], ['Зарплата в месяц', 'compensation'], ['Дата выхода', 'startDate'], ['Формат работы', 'format']]) {
      await page.getByLabel(label, { exact: true }).fill(values[key]);
    }
    await page.getByRole('textbox', { name: 'Ожидания', exact: true }).fill(values.expectations);
  };
  const shot = async name => {
    const path = `output/playwright/employer-stage-card-20261014/${name}.png`;
    await page.screenshot({ path, fullPage: true }); screenshots.push(path);
  };
  let loseNextCreate = false, delayedState;
  const routeHandler = async route => {
    const request = route.request();
    const url = new URL(request.url());
    const relative = url.pathname.replace(/^\/api\/hr\//, '') + url.search;
    if (relative.startsWith('employer/session')) {
      return route.fulfill({ json: { token: config.token, state: await read() } });
    }
    const payload = request.postData() ? request.postDataJSON() : null;
    if (relative === 'employer/action') actions.push(payload);
    if (relative.includes('materials')) materials.push({ path: relative, ...payload });
    const response = await route.fetch({
      url: new URL(relative, config.apiBase).href,
      headers: { authorization: `Bearer ${config.token}`, 'content-type': 'application/json' },
    });
    if (delayedState && !delayedState.used && relative.startsWith('employer/state?') && url.searchParams.get('vacancyId') === delayedState.vacancyId) {
      const held = delayedState; held.used = true; held.started();
      await held.resumed;
    }
    if (loseNextCreate && relative === 'employer/action' && payload?.action === 'test.create') {
      if (!response.ok()) throw Error('Lost-response fixture must commit before disconnect');
      loseNextCreate = false;
      await response.dispose();
      return route.abort('connectionreset');
    }
    return route.fulfill({ response });
  };
  await page.route('**/api/hr/**', routeHandler);
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await goto('shortlist');
    await exactCard(config.applicationId, config.vacancy.id);
    assert(await tabs().count() === 5, 'Two meetings and two tests coexist after initial interview');
    assert(await page.locator('.cj-feedback-stage').count() === 0, 'No mandatory global feedback screen');
    assert(await page.getByText(config.proposedName, { exact: true }).count() === 0, 'Selected card excludes proposed candidate');

    const sentinels = ['ONLY_FIRST_MEETING', 'ONLY_SECOND_MEETING', 'ONLY_FIRST_TEST', 'ONLY_SECOND_TEST'];
    const baseline = await person();
    const actionStart = actions.length;
    for (const [id, sentinel] of [
      [`meeting:${config.firstMeetingId}`, sentinels[0]], [`meeting:${config.secondMeetingId}`, sentinels[1]],
      [`test:${config.firstTestId}`, sentinels[2]], [`test:${config.secondTestId}`, sentinels[3]],
    ]) {
      await selectStage(id);
      const text = await panel().innerText();
      assert(text.includes(sentinel), `Selected ${id} renders its own saved result`);
      assert(sentinels.filter(value => value !== sentinel).every(value => !text.includes(value)), 'Other stage results are absent');
      assert(await panel().locator('.esc-interview').count() === 0, 'Recruiter interview is absent outside first stage');
    }
    await selectStage(`interview:${config.initialInterviewId}`);
    assert(await panel().locator('.esc-interview').count() === 1, 'Recruiter interview is on first stage');
    assert(actions.length === actionStart, 'Browsing history sends no workflow mutation');
    assert(JSON.stringify((await person()).stages) === JSON.stringify(baseline.stages), 'Browsing history preserves entire chain');
    assert(materials.filter(item => item.path === 'employer/materials').every(item =>
      [config.firstMeetingId, config.secondMeetingId].includes(item.meetingId)), 'Meeting material requests retain exact historical meeting ID');
    const requestedMeetings = new Set(materials.filter(item => item.path === 'employer/materials').map(item => item.meetingId));
    assert(requestedMeetings.has(config.firstMeetingId) && requestedMeetings.has(config.secondMeetingId), 'Both historical meetings load their own material endpoint');

    // Manual tabs: arrows move focus; Enter changes only the displayed stage.
    await tabs().first().focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.activeElement === document.querySelectorAll('.esc-stage-tab')[1]);
    assert(await tabs().first().getAttribute('aria-selected') === 'true', 'Arrow key does not silently activate a stage');
    await page.keyboard.press('Enter');
    await page.locator('.esc-stage-tab:nth-child(2)[aria-selected="true"]').waitFor();
    assert(await tabs().nth(1).getAttribute('aria-selected') === 'true', 'Enter activates the focused stage');
    await button('Добавить следующий этап').focus();
    await page.keyboard.press('Enter');
    await page.getByRole('menuitem', { name: 'Встреча', exact: true }).waitFor();
    assert(await page.getByRole('menuitem').count() === 3, 'Plus discloses meeting, test and offer');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === 'Добавить следующий этап');
    assert(await button('Добавить следующий этап').evaluate(el => document.activeElement === el), 'Escape returns focus to plus');

    // A committed POST whose response is lost must be safe to retry in this form.
    await button('Добавить следующий этап').click();
    await page.getByRole('menuitem', { name: 'Тестовое', exact: true }).click();
    await page.getByRole('heading', { name: 'Новое тестовое', exact: true }).waitFor();
    await page.getByLabel('Задание', { exact: true }).fill('Третье тестовое · потерянный ответ');
    await page.getByRole('textbox', { name: 'Что нужно сделать', exact: true }).fill('ONLY_THIRD_TEST_INSTRUCTIONS');
    await button('Добавить ссылку на материал').click();
    await page.getByLabel('Название материала', { exact: true }).fill('ONLY_THIRD_TEST_DOCUMENT');
    await page.getByLabel('Ссылка на материал', { exact: true }).fill('https://example.test/test-document');
    loseNextCreate = true;
    await button('Добавить тестовое').click();
    await page.getByRole('alert').filter({ hasText: /сохранить|связ|соедин|fetch/i }).first().waitFor();
    assert(await page.getByLabel('Задание', { exact: true }).inputValue() === 'Третье тестовое · потерянный ответ', 'Failed response retains editor draft');
    await button('Добавить тестовое').click();
    await page.getByRole('heading', { name: 'Третье тестовое · потерянный ответ', exact: true }).waitFor();
    const testCommands = actions.filter(item => item.action === 'test.create');
    assert(testCommands.length === 2 && testCommands[0].requestId === testCommands[1].requestId, 'Retry preserves requestId after committed lost response');
    let saved = await person();
    assert(saved.stages.filter(stage => stage.kind === 'test').length === 3, 'Lost response creates exactly one additional test');
    assert(saved.stages.length === 6, 'Earlier stages survive adding third test');
    const thirdTestId = saved.stages.find(stage => stage.title === 'Третье тестовое · потерянный ответ').id;

    await page.reload();
    await exactCard(config.applicationId, config.vacancy.id);
    assert(await tabs().count() === 6, 'Reload restores complete persisted stage chain');
    await selectStage(`meeting:${config.firstMeetingId}`);
    await selectStage(thirdTestId);
    assert((await panel().innerText()).includes('ONLY_THIRD_TEST_INSTRUCTIONS'), 'Returning from history restores latest stage materials');
    await page.goBack();
    await page.locator(`[data-stage-id="meeting:${config.firstMeetingId}"]`).waitFor();
    assert(await tabs().count() === 6, 'Browser Back opens past materials without removing newer stages');
    await page.goForward();
    await page.locator(`[data-stage-id="${thirdTestId}"]`).waitFor();

    await button('Добавить следующий этап').click();
    await page.getByRole('menuitem', { name: 'Оффер', exact: true }).click();
    const offerValues = { role: 'QA · Менеджер', compensation: '190 000 ₽', startDate: config.startDate, format: 'Москва · офис', expectations: 'ONLY_OFFER_EXPECTATIONS' };
    await fillOffer(offerValues);
    await button('Сохранить оффер').click();
    await page.getByRole('heading', { name: 'Предложение для кандидата', exact: true }).waitFor();
    saved = await person();
    const originalOffer = saved.offer;

    // Saving identical terms through + is a no-op, not a deferred stage selection.
    await button('Добавить следующий этап').click();
    await page.getByRole('menuitem', { name: 'Оффер', exact: true }).click();
    await fillOffer(offerValues);
    await button('Сохранить оффер').click();
    await page.getByRole('heading', { name: 'Предложение для кандидата', exact: true }).waitFor();
    saved = await person();
    assert(saved.offer.id === originalOffer.id && saved.offers.length === 1, 'No-op offer from plus keeps the same immutable version');
    await button('Добавить следующий этап').click();
    await page.getByRole('menuitem', { name: 'Тестовое', exact: true }).click();
    await page.getByLabel('Задание', { exact: true }).fill('UNSAVED_DRAFT_AFTER_NOOP_OFFER');
    for (let i = 0; i < 2; i++) await page.waitForResponse(response => response.url().includes('/api/hr/employer/state?') && response.ok());
    assert(await page.getByLabel('Задание', { exact: true }).inputValue() === 'UNSAVED_DRAFT_AFTER_NOOP_OFFER', 'Polling after no-op offer keeps a newly opened test draft');
    await button('Отмена').click();

    // Editing an offer creates another version while the earlier stage survives.
    await button('Изменить оффер').click();
    await page.getByLabel('Зарплата в месяц', { exact: true }).fill('210 000 ₽');
    await button('Добавить ссылку на материал').click();
    await page.getByLabel('Название материала', { exact: true }).fill('ONLY_OFFER_DOCUMENT');
    await page.getByLabel('Ссылка на материал', { exact: true }).fill('https://example.test/offer-document');
    await button('Сохранить оффер').click();
    await page.getByRole('heading', { name: 'Предложение для кандидата', exact: true }).waitFor();
    assert((await panel().innerText()).includes('ONLY_OFFER_EXPECTATIONS'), 'Offer renders saved proposal');
    const offerText = await panel().innerText();
    assert(sentinels.every(value => !offerText.includes(value)) && await panel().locator('.esc-interview,.cj-materials').count() === 0, 'Offer contains no meeting/test/recruiter materials');
    assert(offerText.includes('ONLY_OFFER_DOCUMENT') && !offerText.includes('ONLY_THIRD_TEST_DOCUMENT'), 'Offer documents are scoped to its own stage');
    saved = await person();
    assert(saved.offers.length === 2 && saved.offers.find(offer => offer.id === originalOffer.id).compensation === '190 000 ₽', 'Editing offer preserves earlier terms as an immutable history stage');
    assert(saved.feedback === null, 'Saving offer does not fabricate feedback');

    await button('Добавить следующий этап').click();
    await page.getByRole('menuitem', { name: 'Встреча', exact: true }).click();
    await page.getByRole('heading', { name: 'Назначить встречу', exact: true }).waitFor();
    const slot = page.getByRole('radio').first();
    assert(await slot.isEnabled(), 'Completed meeting does not block another meeting');
    await slot.click();
    await button('Предложить время').click();
    await exactCard(config.applicationId, config.vacancy.id);
    await page.locator('.esc-stage-editor').waitFor({ state: 'hidden' });
    saved = await person();
    assert(saved.stages.filter(stage => stage.kind === 'meeting').length === 3, 'Third meeting is added without replacing completed meetings');
    assert(saved.stages.some(stage => stage.id === `meeting:${config.firstMeetingId}`), 'First meeting remains after third is scheduled');
    assert(saved.meeting.status === 'pending', 'New meeting awaits separate candidate confirmation');
    await activeStageVisible('Newly added meeting is visible in the stage strip');
    await shot('desktop-chain');

    // Leave two concrete same-document history entries before switching vacancy.
    await selectStage(`meeting:${config.firstMeetingId}`);
    await selectStage(`meeting:${saved.meeting.id}`);

    // Global calendar opens the same candidate's other application, not this card.
    await page.getByRole('navigation', { name: 'Кабинет нанимающего' }).getByRole('button', { name: 'Встречи', exact: true }).click();
    await page.getByRole('heading', { name: 'Встречи', exact: true }).waitFor();
    const event = page.locator('.ew-agenda-event').filter({ hasText: config.otherVacancy.title || config.otherVacancy.role });
    await event.getByRole('button', { name: `Открыть встречу: ${config.candidateName}`, exact: true }).click();
    await exactCard(config.otherApplicationId, config.otherVacancy.id);
    assert(await tabs().count() === 2, 'Same candidate in another vacancy has independent stage chain');
    assert(!(await card.innerText()).includes('ONLY_FIRST_TEST'), 'Other application does not inherit current application results');
    await page.goBack();
    await exactCard(config.applicationId, config.vacancy.id);
    await page.locator(`[data-stage-id="meeting:${config.firstMeetingId}"]`).waitFor();
    assert((await panel().innerText()).includes('ONLY_FIRST_MEETING'), 'Browser Back restores exact vacancy, application and stage materials');
    await page.goForward();
    await exactCard(config.otherApplicationId, config.otherVacancy.id);
    assert(await tabs().count() === 2, 'Browser Forward restores the other vacancy independent chain');

    // Hold the first scope read so a later Forward is processed while Back is in flight.
    let stateStarted, resumeState;
    const started = new Promise(resolve => { stateStarted = resolve; });
    const resumed = new Promise(resolve => { resumeState = resolve; });
    delayedState = { vacancyId: config.vacancy.id, started: stateStarted, resumed };
    await page.goBack();
    await started;
    const forwardLoaded = page.waitForResponse(response => response.url().includes('/api/hr/employer/state?') && new URL(response.url()).searchParams.get('vacancyId') === config.otherVacancy.id && response.ok());
    await page.goForward(); resumeState();
    await forwardLoaded;
    await exactCard(config.otherApplicationId, config.otherVacancy.id);
    assert(await tabs().count() === 2, 'Rapid Back/Forward keeps the latest URL and matching application state');
    delayedState = undefined;

    await goto('shortlist');
    await exactCard(config.applicationId, config.vacancy.id);
    await page.setViewportSize({ width: 390, height: 844 });
    await fit('Stage card');
    await activeStageVisible('Selected latest stage remains visible after narrow viewport resize');
    await button('Добавить следующий этап').click();
    const menuBox = await page.getByRole('menu').boundingBox();
    assert(menuBox && menuBox.x >= 0 && menuBox.x + menuBox.width <= 390, 'Touch next-step menu fits narrow viewport');
    await page.keyboard.press('Escape');
    await page.reload();
    await exactCard(config.applicationId, config.vacancy.id);
    await activeStageVisible('Mobile reload reveals selected latest stage without manual scrolling');
    await shot('mobile-chain');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await selectStage(`meeting:${config.firstMeetingId}`);
    await activeStageVisible('Reduced-motion history selection remains visible in the stage strip');
    const motion = await card.evaluate(el => [...el.querySelectorAll('*')].some(child => {
      const style = getComputedStyle(child);
      return style.animationName !== 'none' && style.animationDuration.split(',').some(time => parseFloat(time) > 0.01);
    }));
    assert(!motion, 'Reduced motion has no continuing card animation');
    await fit('Reduced-motion history');

    await page.setViewportSize({ width: 1440, height: 1000 });
    await goto('tasks');
    const emptyTask = page.locator('.cj-task').filter({ hasText: config.emptyVacancy.title || config.emptyVacancy.role });
    await emptyTask.getByRole('button').first().click();
    await page.waitForURL(url => url.searchParams.get('vacancyId') === config.emptyVacancy.id && url.searchParams.get('pool') === 'selected');
    await page.getByRole('heading', { name: 'Выбранных кандидатов пока нет', exact: true }).waitFor();
    assert(new URL(page.url()).searchParams.get('pool') === 'selected', 'Vacancy without selected people stays in selected context');
    assert(await card.count() === 0 && await page.getByText(config.proposedName, { exact: true }).count() === 0, 'Empty selected vacancy never substitutes proposed candidate');
    await goto('shortlist', config.vacancy.id, 'missing-application-for-qa');
    await page.getByRole('heading', { name: 'Кандидат недоступен', exact: true }).waitFor();
    assert(await card.count() === 0, 'Unknown explicit application never silently falls back to another person');
    assert(errors.length === 0, 'No browser JavaScript exceptions');
    return { status: 'passed', boundary: config.fixture, checks, actions: actions.map(({ action, applicationId, vacancyId, meetingId, requestId }) => ({ action, applicationId, vacancyId, meetingId, requestId })), materials, screenshots, errors };
  } catch (error) {
    await shot('failure');
    return { status: 'failed', boundary: config.fixture, checks, failure: error.message, screenshots, errors };
  } finally {
    await page.unroute('**/api/hr/**', routeHandler);
    page.off('pageerror', onError);
  }
}
