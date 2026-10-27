// Reproducible browser check. Run in an isolated playwright-cli session on the
// employer story with: playwright-cli run-code "$(cat scripts/employer-pipeline-check.js)".
// Real delivery/calls are intentionally outside this local UI verification.
async (page) => {
  const storageKey = 'hr-vision-employer-pipeline-v1';
  const checks = [];
  const errors = [];
  const writes = [];
  const screenshots = [];
  const started = new Date().toISOString();
  const assert = (value, message) => {
    if (!value) throw new Error(message);
    checks.push(message);
  };
  const errorHandler = error => errors.push(error.message);
  const consoleHandler = message => { if (message.type() === 'error') errors.push(message.text()); };
  const requestHandler = request => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes.push({ method: request.method(), url: request.url() });
  };
  page.on('pageerror', errorHandler);
  page.on('console', consoleHandler);
  page.on('request', requestHandler);
  const button = name => page.getByRole('button', { name, exact: true });
  const stored = () => page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), storageKey);
  const fit = async label => {
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const metrics = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
      assert(metrics.scroll <= metrics.width + 1, `${label}: no horizontal overflow at ${width}px (${metrics.scroll}px)`);
    }
  };
  const screenshot = async name => {
    const path = `output/playwright/pipeline-20261004/${name}.png`;
    await page.screenshot({ path, fullPage: true });
    screenshots.push(path);
  };
  const reset = async () => {
    const settings = page.locator('.ag-view-settings');
    if ((await settings.getAttribute('open')) === null) await page.getByLabel('Настройки просмотра', { exact: true }).click();
    await button('Сбросить пример').click();
    if ((await settings.getAttribute('open')) !== null) await page.getByLabel('Настройки просмотра', { exact: true }).click();
    await page.getByRole('heading', { name: 'Задачи найма', exact: true }).waitFor();
  };
  const navigate = text => page.getByRole('navigation', { name: 'Этапы найма' }).getByRole('button').filter({ hasText: text }).click();
  const people = [{ id: 'anna', name: 'Анна Миронова' }, { id: 'mikhail', name: 'Михаил Белов' }, { id: 'elena', name: 'Елена Орлова' }];
  const futureDate = offset => new Date(Date.now() + offset * 86400000).toISOString().slice(0, 10);
  const choose = async person => {
    await navigate('Подборка');
    await button(`Открыть: ${person.name}`).click();
  };
  const confirmAndFinish = async person => {
    const scenario = page.locator('details.hp-scenario');
    await scenario.locator('summary').click();
    await button('Смоделировать подтверждение').click();
    assert((await stored()).byCandidate[person.id].meeting.status === 'confirmed', `${person.name}: confirmation is a separate explicit step`);
    await button('Открыть комнату').click();
    assert(await page.getByRole('heading', { name: 'Видеокомната пока не подключена', exact: true }).isVisible(), `${person.name}: room states the missing real provider`);
    await fit(`Meeting room ${person.id}`);
    await button('Показать завершение встречи').click();
    await page.getByRole('heading', { name: 'Как прошла встреча?', exact: true }).waitFor();
    assert((await stored()).byCandidate[person.id].meeting.status === 'completed', `${person.name}: simulated completion opens mandatory feedback`);
  };
  const fillFeedback = async person => {
    await page.locator('#feedback-example').fill(`${person.name}: разобрал причины ухода клиентов и вернул повторные продажи.`);
    await page.locator('#feedback-doubts').fill(`${person.name}: нужно проверить самостоятельность результата.`);
    await page.locator('#feedback-check').fill(`${person.name}: запросить пример плана работы с базой.`);
    await button('Сохранить фидбек').click();
    await page.getByRole('heading', { name: 'Разбор вашей встречи', exact: true }).waitFor();
    const state = (await stored()).byCandidate[person.id];
    assert(Boolean(state.feedback.savedAt), `${person.name}: submitted feedback saved`);
    assert((await page.locator('.hp-meeting-evidence').innerText()).includes('Не рассчитана'), `${person.name}: assessment has no invented score`);
    assert((await page.locator('.hp-meeting-evidence').innerText()).includes('Записи и расшифровки пока нет'), `${person.name}: own meeting recording is not confused with HR interview`);
  };
  const prepareOffer = async person => {
    await page.getByRole('group', { name: 'Выбрать получателя оффера' }).getByRole('button', { name: person.name, exact: true }).click();
    await page.getByLabel('Вознаграждение', { exact: true }).fill('170 000 ₽ на руки в месяц + согласованный бонус');
    await page.getByLabel('Предполагаемая дата начала', { exact: true }).fill(futureDate(20));
    await page.getByLabel('Результат работы и KPI', { exact: true }).fill('За 30 дней подготовить план возврата клиентов и согласовать измеримые цели по повторным продажам.');
    await button('Посмотреть оффер').click();
    await page.getByRole('heading', { name: 'Проверьте предложение', exact: true }).waitFor();
    assert((await page.locator('.hp-delivery-note').innerText()).includes('письмо не отправляется'), `${person.name}: preview discloses simulated delivery before action`);
    await fit(`Offer preview ${person.id}`);
    await button('Отправить оффер').click();
    await page.getByRole('heading', { name: 'Ожидаем ответ', exact: true }).waitFor();
    const state = await stored();
    assert(state.byCandidate[person.id].offer.status === 'sent', `${person.name}: offer enters local pending state`);
    assert(Object.values(state.byCandidate).filter(flow => ['sent', 'accepted'].includes(flow.offer.status)).length === 1, `${person.name}: exactly one active offer`);
    assert((await page.locator('.hp-offer').innerText()).includes('письмо не отправлялось'), `${person.name}: pending state does not claim external success`);
  };
  try {
    throw new Error('Verification script is being prepared; full UI contract is not ready yet.');
  } catch (error) {
    return { status: 'failed', started, finished: new Date().toISOString(), url: page.url(), passed: checks.length, checks, errors, writes, screenshots, failure: error.message };
  } finally {
    page.off('pageerror', errorHandler);
    page.off('console', consoleHandler);
    page.off('request', requestHandler);
  }
}
