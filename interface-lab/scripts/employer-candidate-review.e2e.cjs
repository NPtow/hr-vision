// Materialize __LOCAL_REVIEW_FIXTURE__ from test_employer_candidate_review_acceptance
// --serve-fixture into an ignored local output copy. Run via playwright-cli run-code.
// Real local HTTP/temporary SQLite; only the external video/file resolver is fake.
// Never inject a production session, use a production origin, or send real messages.
async page => {
  const config = __LOCAL_REVIEW_FIXTURE__;
  const local = url => ['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname);
  const uiOrigin = config.uiOrigin || 'http://127.0.0.1:6023';
  if (!config.localOnly || !local(config.apiBase) || !local(uiOrigin) || !config.videoPath) throw Error('Complete local review fixture required');
  const checks = [], errors = [], commands = [], materials = [], screenshots = [];
  const assert = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const onError = error => errors.push(error.message);
  page.on('pageerror', onError);
  const api = async (path, data) => {
    const response = await page.request.fetch(new URL(path, config.apiBase).href, {
      method: data ? 'POST' : 'GET', data,
      headers: { authorization: `Bearer ${config.token}`, 'content-type': 'application/json' },
    });
    const body = await response.json();
    if (!response.ok()) throw Error(`Local API ${path}: ${response.status()}`);
    return body;
  };
  const state = (vid = config.vacancy.id) => api('employer/state?vacancyId=' + encodeURIComponent(vid));
  const person = async (aid = config.alpha.applicationId, vid = config.vacancy.id) => (await state(vid)).candidates[aid];
  const intake = async () => (await api('employer/intake?vacancyId=' + config.vacancy.id)).candidates;
  const card = () => page.locator('.ecp-profile');
  const button = name => page.getByRole('button', { name, exact: true });
  const chapter = index => page.locator('.ir-candidate-chapters > ol > li').nth(index);
  const exact = async id => {
    await page.locator(`.ecp-profile[data-candidate-id="${id}"]`).waitFor({ state: 'visible' });
    assert(await card().count() === 1, 'Only one proposed-candidate profile is rendered');
  };
  const goto = async (aid = config.alpha.applicationId, vid = config.vacancy.id, pool = 'proposed') => {
    const url = new URL('/iframe.html', uiOrigin);
    Object.entries({ id: 'hr-vision-product--manager', viewMode: 'story', screen: 'shortlist', vacancyId: vid, applicationId: aid, pool }).forEach(([key, value]) => url.searchParams.set(key, value));
    await page.goto(url.href);
  };
  const select = async (name, id) => {
    const row = page.locator('.ew-person-row').filter({ hasText: name });
    await row.waitFor({ state: 'attached' });
    if (!await row.isVisible()) await page.getByRole('button', { name: 'К списку', exact: true }).click();
    await row.click(); await exact(id);
  };
  const readyVideo = async () => {
    await page.waitForFunction(() => {
      const video = document.querySelector('.ecp-profile video');
      return video && video.readyState >= 1 && Number.isFinite(video.duration) && video.duration > 80;
    });
  };
  const fit = async label => {
    const size = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
    assert(size.scroll <= size.width + 1, `${label}: no page-level horizontal overflow`);
  };
  const shot = async name => {
    const path = `output/playwright/employer-candidate-review-20261014/${name}.png`;
    await page.screenshot({ path, fullPage: true }); screenshots.push(path);
  };
  let loseComment = false, rejectBeforeCommit = false, delayedMaterial, delayedRejection;
  const routeHandler = async route => {
    const request = route.request();
    const url = new URL(request.url());
    const relative = url.pathname.replace(/^\/api\/hr\//, '') + url.search;
    if (relative.startsWith('employer/session')) return route.fulfill({ json: { token: config.token, state: await state() } });
    const body = request.postData() ? request.postDataJSON() : null;
    if (relative === 'employer/action' || relative === 'employer/intake/comment' || relative === 'employer/intake/review') commands.push({ path: relative, body });
    if (relative === 'employer/screening/materials') materials.push(body);
    if (rejectBeforeCommit && relative === 'employer/action' && body?.action === 'review.reject') {
      rejectBeforeCommit = false;
      return route.fulfill({ status: 503, json: { error: 'Локальная проверка: временно не удалось сохранить отказ.' } });
    }
    const response = await route.fetch({ url: new URL(relative, config.apiBase).href,
      headers: { authorization: `Bearer ${config.token}`, 'content-type': 'application/json' } });
    if (delayedMaterial && !delayedMaterial.used && relative === 'employer/screening/materials' && body?.applicationId === delayedMaterial.applicationId) {
      const held = delayedMaterial; held.used = true; held.started(); await held.resumed;
    }
    if (delayedRejection && !delayedRejection.used && relative === 'employer/action' && body?.action === 'review.reject') {
      const held = delayedRejection; held.used = true; held.started(); await held.resumed;
    }
    if (loseComment && relative === 'employer/action' && body?.action === 'screening.comment.add') {
      if (!response.ok()) throw Error('Lost-response fixture requires a committed comment');
      loseComment = false; await response.dispose(); return route.abort('connectionreset');
    }
    return route.fulfill({ response });
  };
  const videoHandler = async route => {
    const response = await route.fetch({ url: new URL('_qa/video', config.apiBase).href,
      headers: { range: route.request().headers().range || 'bytes=0-' } });
    return route.fulfill({ response });
  };
  await page.route('**/api/hr/**', routeHandler);
  await page.route('https://media.example/**', videoHandler);
  try {
    await page.setViewportSize({ width: 1440, height: 1080 });
    await goto(); await exact(config.alpha.applicationId); await readyVideo();
    const originalSelected = await person(config.selected.applicationId);
    assert(await page.locator('.ir-candidate-chapters > ol > li').count() === 3, 'Canonical VTT produces three actual timestamp chapters');
    assert((await card().innerText()).includes('ONLY_ALPHA_PROFILE') && !(await card().innerText()).includes('ONLY_BETA_'), 'Profile and transcript match the selected candidate');
    assert(await card().getByRole('button', { name: 'Назначить встречу', exact: true }).isVisible() && await card().getByRole('button', { name: 'Отказать', exact: true }).isVisible(), 'Decision exposes meeting and reject in the review card');
    const downloadEvent = page.waitForEvent('download');
    await button('Скачать резюме').click();
    assert((await downloadEvent).suggestedFilename() === 'acceptance.txt', 'Original CV download is wired to the real scoped endpoint');

    // Every existing chapter can hold its own saved comment.
    for (const [index, seconds] of [0, 12, 40].entries()) {
      const row = chapter(index);
      await row.locator('.ir-candidate-chapter').focus(); await page.keyboard.press('Enter');
      await page.waitForFunction(expected => Math.abs(document.querySelector('.ecp-profile video').currentTime - expected) < 0.2, seconds);
      assert(true, `Keyboard chapter ${seconds}s seeks the actual synthetic video`);
      const trigger = row.getByRole('button', { name: /^Комментарий к/ });
      await trigger.focus(); await page.keyboard.press('Enter');
      await row.getByRole('textbox').fill(`SAVED_ALPHA_COMMENT_${seconds}`);
      if (seconds === 12) loseComment = true;
      await row.getByRole('button', { name: 'Сохранить комментарий', exact: true }).click();
      if (seconds === 12) {
        await row.getByRole('alert').waitFor();
        assert(await row.getByRole('textbox').inputValue() === 'SAVED_ALPHA_COMMENT_12', 'Lost response keeps timestamp comment draft');
        await row.getByRole('button', { name: 'Сохранить комментарий', exact: true }).click();
      }
      await row.getByRole('button', { name: /Комментарий к.*комментариев: 1/ }).waitFor();
    }
    const savedComments = (await person()).screeningComments;
    assert(savedComments.length === 3, 'Three timestamps persist as three comments, including retry');
    assert(savedComments.every(comment => comment.interviewId === config.alpha.interviewId && comment.recordingId === config.alpha.recordingId), 'Every comment retains exact screening and recording identity');
    const retries = commands.filter(item => item.body?.action === 'screening.comment.add' && item.body.seconds === 12);
    assert(retries.length === 2 && retries[0].body.requestId === retries[1].body.requestId, 'Comment retry reuses the original requestId');
    await page.reload(); await exact(config.alpha.applicationId); await readyVideo();
    for (const [index, seconds] of [0, 12, 40].entries()) {
      const row = chapter(index);
      await row.getByRole('button', { name: /Комментарий к.*комментариев: 1/ }).click();
      assert(await row.getByText(`SAVED_ALPHA_COMMENT_${seconds}`, { exact: true }).isVisible(), `Reload restores comment at ${seconds}s`);
      await row.getByRole('button', { name: 'Отмена', exact: true }).click();
    }
    assert((await person()).feedback === null, 'Review comments do not create meeting feedback');
    await shot('canonical-desktop');

    // The same person's other application has independent private comments.
    await goto(config.otherApplicationId, config.otherVacancy.id); await exact(config.otherApplicationId); await readyVideo();
    assert(await page.getByRole('button', { name: /Комментарий к.*комментариев:/ }).count() === 0, 'Same screening in another vacancy does not expose application comments');
    assert((await person(config.otherApplicationId, config.otherVacancy.id)).screeningComments.length === 0, 'Other vacancy persists an independent empty review');
    await goto(config.beforeVideo.applicationId, config.otherVacancy.id);
    await exact(config.beforeVideo.applicationId); await readyVideo();
    await button('Комментарии к 00:20, 1').click();
    assert(await page.getByText('BEFORE_VIDEO_COMMENT', { exact: true }).isVisible(), 'Comment without a recording ID remains visible when its single video becomes available');
    await button('Отмена').click();

    // Late media from a previously selected profile cannot replace the new one.
    let started, resume;
    const waiting = new Promise(resolve => { started = resolve; });
    const resumed = new Promise(resolve => { resume = resolve; });
    delayedMaterial = { applicationId: config.alpha.applicationId, started, resumed };
    await goto(); await exact(config.alpha.applicationId); await waiting;
    await select(config.beta.name, config.beta.applicationId); await readyVideo();
    resume(); delayedMaterial = undefined;
    await page.getByText('ONLY_BETA_PROFILE', { exact: false }).first().waitFor();
    assert(!(await card().innerText()).includes('ONLY_ALPHA_'), 'Late previous-candidate material response cannot leak into current profile');
    assert(!(await card().innerText()).includes('SAVED_ALPHA_COMMENT'), 'Switching candidate isolates saved timestamp comments');

    // A backend failure preserves all three refusal answers and keeps the person.
    await button('Отказать').focus(); await page.keyboard.press('Enter');
    const refusal = page.getByRole('form', { name: 'Обратная связь при отказе', exact: true });
    assert(await refusal.getByRole('textbox').count() === 3, 'Refusal asks exactly three questions');
    for (const [index, text] of ['BETA_REASON', 'BETA_MISSING', 'BETA_IMPROVE'].entries()) await refusal.getByRole('textbox').nth(index).fill(text);
    rejectBeforeCommit = true;
    await refusal.getByRole('button', { name: 'Сохранить отказ', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: /не удалось|остались/ }).first().waitFor();
    assert(await refusal.getByRole('textbox').nth(2).inputValue() === 'BETA_IMPROVE', 'Failed refusal retains all three answers');
    assert(!!(await person(config.beta.applicationId)), 'Failed refusal keeps candidate in review');
    let rejectionStarted, resumeRejection;
    const rejectionWaiting = new Promise(resolve => { rejectionStarted = resolve; });
    const rejectionResumed = new Promise(resolve => { resumeRejection = resolve; });
    delayedRejection = { started: rejectionStarted, resumed: rejectionResumed };
    await refusal.getByRole('button', { name: 'Сохранить отказ', exact: true }).click();
    await rejectionWaiting;
    await select(config.alpha.name, config.alpha.applicationId);
    resumeRejection(); delayedRejection = undefined;
    await page.locator('.ew-person-row').filter({ hasText: config.beta.name }).waitFor({ state: 'hidden' });
    await exact(config.alpha.applicationId);
    assert(await page.getByRole('heading', { name: 'Отказ сохранён', exact: true }).count() === 0, 'Late rejection result does not replace a newly selected candidate');
    assert(await page.locator('.ew-person-row').filter({ hasText: config.beta.name }).count() === 0, 'Successful refusal removes only the rejected candidate from the visible queue');
    assert(!(await state()).candidates[config.beta.applicationId], 'Refusal persists after real API read');
    assert((await person()).screeningComments.length === 3, 'Rejecting another candidate preserves prior comments');

    // Imported cards have real source material and no invented transcript chapters.
    await select(config.prepared.name, config.prepared.id); await readyVideo();
    assert(await page.locator('.ir-candidate-chapters > ol > li').count() === 0, 'Prepared import without transcript does not invent chapters');
    assert(!(await card().innerText()).includes('ONLY_ALPHA_'), 'Prepared recording does not inherit another candidate transcript');
    await button('Открыть').click();
    assert(await page.getByText(/ONLY_PREPARED_CV/).isVisible(), 'Prepared CV remains available inside its own profile');
    await button('Свернуть').click();
    const position = page.getByRole('slider', { name: 'Позиция видеозаписи', exact: true });
    await position.focus(); await page.keyboard.press('End');
    const currentComment = page.getByRole('button', { name: 'Комментарий к 01:29', exact: true });
    await currentComment.click();
    await page.getByRole('textbox', { name: 'Комментарий к 01:29', exact: true }).fill('PREPARED_CURRENT_TIME_COMMENT');
    await button('Сохранить комментарий').click();
    await page.getByRole('textbox', { name: 'Комментарий к 01:29', exact: true }).waitFor({ state: 'hidden' });
    let prepared = (await intake()).find(candidate => candidate.id === config.prepared.id);
    assert(prepared.screeningComments.length === 1 && prepared.screeningComments[0].seconds === 89, 'Prepared current-time comment persists against stored recording identity');
    assert(!(await state()).candidates[config.prepared.id], 'Prepared comment does not fabricate a canonical application');
    assert(new URL(page.url()).searchParams.get('intakeId') === config.prepared.id, 'Prepared selection has its exact identity in the URL');
    await page.reload(); await exact(config.prepared.id); await readyVideo();
    assert(new URL(page.url()).searchParams.get('intakeId') === config.prepared.id, 'Reload preserves the prepared candidate rather than opening another application');
    await select(config.alpha.name, config.alpha.applicationId);
    await page.goBack(); await exact(config.prepared.id); await readyVideo();
    assert(new URL(page.url()).searchParams.get('intakeId') === config.prepared.id, 'Browser Back restores the exact prepared profile');
    await button('Комментарии к 01:29, 1').click();
    assert(await page.getByText('PREPARED_CURRENT_TIME_COMMENT', { exact: true }).isVisible(), 'Prepared timestamp comment survives reload');
    await button('Отмена').click();

    await page.setViewportSize({ width: 390, height: 844 });
    if (!await card().isVisible()) await select(config.prepared.name, config.prepared.id);
    await fit('Prepared mobile card');
    await shot('prepared-mobile');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const animated = await card().evaluate(el => [...el.querySelectorAll('*')].some(child => {
      const style = getComputedStyle(child);
      return style.animationName !== 'none' && style.animationDuration.split(',').some(time => parseFloat(time) > 0.01);
    }));
    assert(!animated, 'Reduced motion has no continuing profile animation');
    await button('Отказать').click();
    const importedRefusal = page.getByRole('form', { name: 'Обратная связь при отказе', exact: true });
    for (const [index, text] of ['PREPARED_REASON', 'PREPARED_MISSING', 'PREPARED_IMPROVE'].entries()) await importedRefusal.getByRole('textbox').nth(index).fill(text);
    await importedRefusal.getByRole('button', { name: 'Сохранить отказ', exact: true }).click();
    await page.getByText('Кандидат в архиве.', { exact: true }).waitFor();
    prepared = (await intake()).find(candidate => candidate.id === config.prepared.id);
    assert(prepared.archived && prepared.reviewRejection.reason === 'PREPARED_REASON', 'Prepared refusal archives its own source and persists separate answers');
    assert(await page.locator('.ew-person-row').filter({ hasText: config.prepared.name }).count() === 0, 'Archived prepared candidate is absent from the active queue');
    await button('Комментарии к 01:29, 1').click();
    assert(await page.getByText('PREPARED_CURRENT_TIME_COMMENT', { exact: true }).isVisible(), 'Archived prepared profile retains readable saved comments');
    assert(await card().getByRole('textbox').count() === 0 && await card().getByRole('button', { name: 'Сохранить комментарий', exact: true }).count() === 0, 'Archived timestamp comments are read-only');

    // Cancelling scheduling stays in the proposal pool; success opens the same card.
    await page.setViewportSize({ width: 1440, height: 1080 });
    await goto(); await exact(config.alpha.applicationId);
    await button('Назначить встречу').click();
    await page.getByRole('heading', { name: 'Назначить встречу', exact: true }).waitFor();
    await button('К кандидатам').click();
    await exact(config.alpha.applicationId);
    assert((await person()).reviewState === 'proposed', 'Cancelled scheduling keeps the candidate under review');
    await button('Назначить встречу').click();
    await page.getByRole('radio').first().click();
    await button('Предложить время').click();
    await page.locator(`.esc-card[data-candidate-id="${config.alpha.applicationId}"]`).waitFor();
    assert(new URL(page.url()).searchParams.get('pool') === 'selected', 'Successful scheduling opens selected context for the same application');
    assert((await person()).screeningComments.length === 3, 'Promotion to selected keeps review comments');
    const currentSelected = await person(config.selected.applicationId);
    assert(JSON.stringify(currentSelected.stages) === JSON.stringify(originalSelected.stages), 'Existing selected-candidate stage history remains unchanged');
    await goto(config.selected.applicationId, config.vacancy.id, 'selected');
    await page.locator(`.esc-card[data-candidate-id="${config.selected.applicationId}"]`).waitFor();
    assert(await page.getByRole('tablist', { name: 'Этапы кандидата', exact: true }).getByRole('tab').count() === 3, 'Existing selected stage workspace remains available');
    await goto('missing-review-application');
    await page.getByRole('heading', { name: 'Кандидат недоступен', exact: true }).waitFor();
    assert(await card().count() === 0, 'Stale explicit review URL never falls back to another candidate');
    assert(materials.every(body => body.applicationId && body.vacancyId && body.candidate === body.applicationId), 'Screening material requests consistently carry exact application scope');
    assert(errors.length === 0, 'No browser JavaScript exceptions');
    return { status: 'passed', boundary: config.fixture, checks, errors, screenshots,
      commands: commands.map(({ path, body }) => ({ path, action: body?.action, applicationId: body?.applicationId, vacancyId: body?.vacancyId, id: body?.id, requestId: body?.requestId })), materials };
  } catch (error) {
    await shot('failure');
    return { status: 'failed', boundary: config.fixture, checks, failure: error.message, errors, screenshots };
  } finally {
    await page.unroute('**/api/hr/**', routeHandler);
    await page.unroute('https://media.example/**', videoHandler);
    page.off('pageerror', onError);
  }
}
