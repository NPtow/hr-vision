// Materialize __LOCAL_REVIEW_FIXTURE__ only in a private local output copy.
// Real temporary employer HTTP/SQLite, four verified source profiles, read-only
// original DSA media. No production session, mutation, message or generated speech.
async page => {
  const config = __LOCAL_REVIEW_FIXTURE__;
  const local = url => ['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname);
  const uiOrigin = config.uiOrigin || 'http://127.0.0.1:6023';
  if (!config.localOnly || !local(config.apiBase) || !local(uiOrigin) || config.profiles?.length !== 4 || !config.artifactDir) throw Error('Private local source fixture required');
  const checks = [], errors = [], commands = [], mediaResponses = [], screenshots = [], decodedFrames = [];
  const assert = (ok, label) => { if (!ok) throw Error(label); checks.push(label); };
  const onError = error => errors.push(error.message);
  const onResponse = response => {
    if (response.url().startsWith('https://kabinet.158-160-179-53.sslip.io/api/video/')) mediaResponses.push({ status: response.status(), range: response.headers()['content-range'] || '' });
  };
  page.on('pageerror', onError); page.on('response', onResponse);
  const api = async path => {
    const response = await page.request.get(new URL(path, config.apiBase).href, { headers: { authorization: `Bearer ${config.token}` } });
    if (!response.ok()) throw Error(`Local API read failed: ${response.status()}`);
    return response.json();
  };
  const state = () => api('employer/state?vacancyId=' + config.vacancy.id);
  const intake = async (vid = config.vacancy.id) => (await api('employer/intake?vacancyId=' + vid)).candidates;
  const sources = await intake();
  const expected = id => sources.find(person => person.id === id).preparedProfile;
  const originalStages = (await state()).candidates[config.selectedApplicationId].stages;
  const card = () => page.locator('.ecp-profile');
  const row = index => card().locator('.ir-candidate-chapters > ol > li').nth(index);
  const exact = async id => {
    await page.locator(`.ecp-profile[data-candidate-id="${id}"]`).waitFor({ state: 'visible' });
    assert(await card().count() === 1, 'Exactly one requested source profile is rendered');
  };
  const goto = async (id, vid = config.vacancy.id) => {
    const url = new URL('/iframe.html', uiOrigin);
    Object.entries({ id: 'hr-vision-product--manager', viewMode: 'story', screen: 'shortlist', pool: 'proposed', vacancyId: vid, intakeId: id }).forEach(([k, v]) => url.searchParams.set(k, v));
    await page.goto(url.href); await exact(id);
  };
  const select = async person => {
    const target = page.locator('.ew-person-row').filter({ hasText: person.name });
    await target.waitFor({ state: 'attached' });
    if (!await target.isVisible()) await page.getByRole('button', { name: 'К списку', exact: true }).click();
    await target.click(); await exact(person.id);
  };
  const ready = async () => page.waitForFunction(() => {
    const video = document.querySelector('.ecp-profile video');
    return video && video.readyState >= 1 && video.duration > 60 && video.videoWidth > 0;
  }, null, { timeout: 45000 });
  const pause = async () => {
    await page.waitForFunction(() => { const video = document.querySelector('.ecp-profile video'); return !video || (!video.seeking && video.readyState >= 2); }, null, { timeout: 45000 });
    const frame = await page.evaluate(() => {
      const video = document.querySelector('.ecp-profile video');
      if (!video || video.paused || !video.requestVideoFrameCallback) return null;
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(Error('No decoded video frame arrived before pause')), 10000);
        video.requestVideoFrameCallback((_now, metadata) => { clearTimeout(timer); resolve({ time: metadata.mediaTime, frames: metadata.presentedFrames }); });
      });
    });
    if (frame) decodedFrames.push(frame);
    const control = card().getByRole('button', { name: 'Пауза', exact: true });
    if (await control.isVisible()) await control.click();
  };
  const shot = async name => {
    const path = `${config.artifactDir}/${name}.png`;
    await page.screenshot({ path, fullPage: true }); screenshots.push(path);
  };
  const clock = value => value.split(':').reduce((n, part) => n * 60 + Number(part), 0);
  const normalize = value => value.replace(/\s+/g, ' ').trim();
  // Independent reference: literal saved-source lines; no generated transcript.
  const cues = profile => profile.transcriptText.split(/\r?\n/).flatMap(line => {
    const match = line.match(/^\[(\d+(?::\d{2}){1,2})-(\d+(?::\d{2}){1,2})\]\s+([РК]):\s*(.+)$/u);
    return match ? [{ start: clock(match[1]), end: clock(match[2]), text: match[4] }] : [];
  });
  const excerpt = async (profile, index) => {
    const chapter = profile.chapters[index];
    const sourceText = cues(profile).filter(cue => cue.start === cue.end ? cue.start >= chapter.seconds && cue.start < chapter.endSeconds : cue.end > chapter.seconds && cue.start < chapter.endSeconds).map(cue => cue.text);
    const actual = await card().locator('[aria-label="Текст выбранного фрагмента"] p').allTextContents();
    assert(sourceText.length > 0 && normalize(actual.join(' ')) === normalize(sourceText.join(' ')), `Chapter ${index + 1}: selected transcript equals all source cues in its exact interval`);
  };
  let loseComment = false, failComment = false, recordingHeld = false, recordingStarted, releaseRecording;
  const recordingWaiting = new Promise(resolve => { recordingStarted = resolve; });
  const recordingReleased = new Promise(resolve => { releaseRecording = resolve; });
  const routeHandler = async route => {
    const request = route.request(), url = new URL(request.url());
    const relative = url.pathname.replace(/^\/api\/hr\//, '') + url.search;
    if (relative.startsWith('employer/session')) return route.fulfill({ json: { token: config.token, state: await state() } });
    if (request.method() === 'POST' && !['employer/intake/comment', 'employer/intake/recording'].includes(relative)) throw Error('Unexpected write in candidate view QA');
    const body = request.postData() ? request.postDataJSON() : null;
    if (relative === 'employer/intake/comment') {
      commands.push(body);
      if (failComment) { failComment = false; return route.fulfill({ status: 503, json: { error: 'Локальная проверка сохранения.' } }); }
    }
    const response = await route.fetch({ url: new URL(relative, config.apiBase).href,
      headers: { authorization: `Bearer ${config.token}`, 'content-type': 'application/json' } });
    if (relative === 'employer/intake/recording' && body?.id === config.profiles[0].id && !recordingHeld) {
      recordingHeld = true; recordingStarted(); await recordingReleased;
    }
    if (relative === 'employer/intake/comment' && loseComment) {
      if (!response.ok()) throw Error('Lost-response fixture requires a committed note');
      loseComment = false; await response.dispose(); return route.abort('connectionreset');
    }
    return route.fulfill({ response });
  };
  await page.route('**/api/hr/**', routeHandler);
  try {
    await page.setViewportSize({ width: 1440, height: 1080 });
    const first = config.profiles[0], profile = expected(first.id);
    await goto(first.id); await recordingWaiting;
    await row(1).locator('.ir-candidate-chapter').click();
    releaseRecording(); await ready();
    await page.waitForFunction(start => { const video = document.querySelector('.ecp-profile video'); return video && !video.paused && video.currentTime >= start && video.currentTime < start + 4; }, profile.chapters[1].seconds, { timeout: 45000 });
    assert(true, 'Selecting a chapter before its URL arrives retains the range and starts at its timestamp');
    await pause(); await excerpt(profile, 1);
    await row(0).locator('.ir-candidate-chapter').click(); await pause();
    assert(await card().locator('video').count() === 1 && await card().locator('.ir-candidate-chapters > ol > li').count() === profile.chapters.length, 'One source recording and its actual chapters are shown');
    assert(!await card().locator('.ir-candidate-full-transcript').evaluate(el => el.open), 'Full actual transcript starts collapsed');
    await excerpt(profile, 0);
    // Real source video: user action seeks, plays, then naturally stops its range.
    const rangeIndex = profile.chapters.reduce((best, item, index, all) => item.endSeconds - item.seconds < all[best].endSeconds - all[best].seconds ? index : best, 0);
    const range = profile.chapters[rangeIndex];
    await card().getByRole('button', { name: 'Выключить звук', exact: true }).click();
    await row(rangeIndex).locator('.ir-candidate-chapter').focus(); await page.keyboard.press('Enter');
    await page.waitForFunction(start => { const v = document.querySelector('.ecp-profile video'); return v && !v.paused && v.currentTime >= start && v.currentTime < start + 3; }, range.seconds, { timeout: 45000 });
    assert(true, 'Keyboard chapter action seeks and plays the real source recording');
    await excerpt(profile, rangeIndex);
    await page.waitForFunction(end => { const v = document.querySelector('.ecp-profile video'); return v && v.paused && Math.abs(v.currentTime - end) < 0.15; }, range.endSeconds, { timeout: (range.endSeconds - range.seconds) * 1000 + 20000 });
    await page.waitForTimeout(400);
    assert(await row(rangeIndex).locator('.ir-candidate-chapter').getAttribute('aria-pressed') === 'true', 'Playback stops at the selected end without auto-next');
    assert(mediaResponses.some(response => response.status === 206 && response.range.startsWith('bytes ')), 'Actual DSA media supports byte-range playback');
    assert(decodedFrames.some(frame => frame.frames > 0), 'Source video delivered an actual decoded frame before pause');
    const full = card().locator('.ir-candidate-full-transcript');
    await full.locator('summary').focus(); await page.keyboard.press('Enter');
    assert(await full.evaluate(el => el.open), 'Full transcript opens separately by keyboard');
    assert(normalize((await full.locator('p').allTextContents()).join(' ')) === normalize(cues(profile).map(cue => cue.text).join(' ')), 'Full transcript preserves the complete actual source words');
    await full.locator('summary').click();

    await row(0).locator('.ir-candidate-chapter').click(); await pause();
    await row(0).getByRole('button', { name: /^Комментарий к/ }).click();
    await row(0).getByRole('textbox').fill('LOCAL_DRAFT_RETAINED');
    await row(1).locator('.ir-candidate-chapter').click(); await pause();
    assert(!await row(0).getByRole('textbox').isVisible() && await row(0).locator('textarea').inputValue() === 'LOCAL_DRAFT_RETAINED', 'Unselected chapter hides its comment but preserves its draft');
    assert(await card().locator('.ir-candidate-chapter-comment:visible').count() === 1, 'Only the selected chapter exposes its comment control');
    await row(0).locator('.ir-candidate-chapter').click(); await pause();
    assert(await row(0).getByRole('textbox').inputValue() === 'LOCAL_DRAFT_RETAINED', 'Returning to the chapter restores its draft');
    await row(0).getByRole('button', { name: 'Отмена', exact: true }).click();
    for (let index = 0; index < 3; index++) {
      await row(index).locator('.ir-candidate-chapter').click(); await pause(); await excerpt(profile, index);
      await row(index).getByRole('button', { name: /^Комментарий к/ }).click();
      await row(index).getByRole('textbox').fill(`LOCAL_SOURCE_COMMENT_${index}`);
      if (index === 1) loseComment = true;
      if (index === 2) failComment = true;
      await row(index).getByRole('button', { name: 'Сохранить комментарий', exact: true }).click();
      if (index > 0) {
        await row(index).getByRole('alert').waitFor();
        assert(await row(index).getByRole('textbox').inputValue() === `LOCAL_SOURCE_COMMENT_${index}`, `Chapter ${index + 1}: failure preserves annotation draft`);
        await row(index).getByRole('button', { name: 'Сохранить комментарий', exact: true }).click();
      }
      await row(index).getByRole('button', { name: /Комментарий к.*комментариев: 1/ }).waitFor();
    }
    const saved = (await intake()).find(person => person.id === first.id).screeningComments;
    assert(saved.length === 4 && new Set(saved.map(comment => comment.recordingId)).size === 1, 'Three chapter notes and one legacy note persist against the exact recording');
    const retries = commands.filter(command => command.seconds === profile.chapters[1].seconds);
    assert(retries.length === 2 && retries[0].requestId === retries[1].requestId, 'Lost-response retry saves one comment with one request identity');
    assert(commands.every(command => command.vacancyId === config.vacancy.id && command.id === first.id && command.recordingIndex === 0), 'Annotation commands retain exact local vacancy, profile and recording index');
    await page.reload(); await exact(first.id); await ready();
    for (let index = 0; index < 3; index++) {
      await row(index).locator('.ir-candidate-chapter').click(); await pause();
      await row(index).getByRole('button', { name: /Комментарий к.*комментариев: 1/ }).click();
      assert(await row(index).getByText(`LOCAL_SOURCE_COMMENT_${index}`, { exact: true }).isVisible(), `Reload restores chapter ${index + 1} note`);
      await row(index).getByRole('button', { name: 'Отмена', exact: true }).click();
    }
    const legacy = card().locator('.ir-candidate-saved-comments');
    assert(!await legacy.evaluate(el => el.open), 'Legacy arbitrary-time notes start collapsed');
    await legacy.locator('summary').click();
    await legacy.getByRole('button', { name: 'Комментарии к 00:00, 1', exact: true }).click();
    assert(await legacy.getByText('LOCAL_EXISTING_ARBITRARY_NOTE', { exact: true }).isVisible(), 'Existing arbitrary-time notes remain readable');
    await legacy.getByRole('button', { name: 'Отмена', exact: true }).click();
    await legacy.locator('summary').click();
    await row(2).locator('.ir-candidate-chapter').click(); await pause();
    await shot('source-desktop');

    await goto(config.sameSourceOtherVacancy.id, config.otherVacancy.id); await ready();
    assert((await intake(config.otherVacancy.id)).find(person => person.id === config.sameSourceOtherVacancy.id).screeningComments.length === 0, 'Same source recording under another vacancy keeps private notes separate');
    await goto(first.id); await ready();
    for (let index = 1; index < config.profiles.length; index++) {
      const person = config.profiles[index], source = expected(person.id);
      await select(person); await ready();
      assert(await card().locator('.ir-candidate-chapters > ol > li').count() === source.chapters.length, `Source profile ${index + 1}: its own chapters are shown`);
      await excerpt(source, 0);
      assert((await intake()).find(candidate => candidate.id === person.id).screeningComments.length === 0, `Source profile ${index + 1}: previous candidate notes do not leak`);
    }
    await select(first); await ready();
    await page.setViewportSize({ width: 390, height: 844 });
    if (!await card().isVisible()) await select(first);
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Mobile ${width}: no horizontal page overflow`);
      await row(1).locator('.ir-candidate-chapter').click(); await pause(); await excerpt(profile, 1);
      await row(1).getByRole('button', { name: /^Комментарий к/ }).click();
      await row(1).getByRole('textbox').fill('LOCAL_MOBILE_DRAFT');
      assert(await row(1).getByRole('textbox').isVisible(), `Mobile ${width}: selected chapter comment is usable`);
      await shot(`source-mobile-${width}`);
      await row(1).getByRole('button', { name: 'Отмена', exact: true }).click();
    }
    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert(!await card().evaluate(el => [...el.querySelectorAll('*')].some(child => {
      const style = getComputedStyle(child); return style.animationName !== 'none' && style.animationDuration.split(',').some(value => parseFloat(value) > 0.01);
    })), 'Reduced motion has no continuing decorative animation');
    assert(JSON.stringify((await state()).candidates[config.selectedApplicationId].stages) === JSON.stringify(originalStages), 'Existing selected-stage history remains untouched');
    const archived = config.profiles[3];
    const archivedResponse = await page.request.post(new URL('employer/intake/archive', config.apiBase).href, {
      headers: { authorization: `Bearer ${config.token}` }, data: { vacancyId: config.vacancy.id, id: archived.id, archived: true },
    });
    assert(archivedResponse.ok(), 'Local archival fixture uses the real scoped API');
    const archivedUrl = new URL(page.url()); archivedUrl.searchParams.set('intakeId', archived.id);
    await page.goto(archivedUrl.href);
    await page.getByRole('heading', { name: 'Кандидат недоступен', exact: true, includeHidden: true }).waitFor({ state: 'attached' });
    assert(await card().count() === 0, 'Explicit archived profile URL never falls back to another person');
    await page.setViewportSize({ width: 1440, height: 1080 });
    await page.getByRole('heading', { name: 'Кандидат недоступен', exact: true }).waitFor();
    assert(await card().count() === 0, 'Archived desktop placeholder is visible without another candidate card');
    assert(errors.length === 0, 'No browser JavaScript exceptions');
    return { status: 'passed', boundary: config.fixture, checks, errors, screenshots, sourceMediaRequests: mediaResponses.length };
  } catch (error) {
    await shot('failure');
    return { status: 'failed', boundary: config.fixture, checks, errors, failure: error.message, screenshots, sourceMediaRequests: mediaResponses.length };
  } finally {
    releaseRecording();
    await pause().catch(() => {});
    await page.unroute('**/api/hr/**', routeHandler);
    page.off('pageerror', onError); page.off('response', onResponse);
  }
}
