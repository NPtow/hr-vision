// Run with playwright-cli run-code --filename. Public HTTPS; isolated fixtures only.
async page => {
  const url = 'https://hr-vision.158-160-179-53.sslip.io/iframe.html?id=hr-vision-employer-menu--gallery&viewMode=story&menu=top';
  const variants = [['sidebar','Боковое меню'],['rail','Компактная панель'],['top','Верхняя строка'],['dock','Плавающее меню'],['launcher','Меню по кнопке']];
  const checks = [], errors = [], apiRequests = [];
  const check = (value, name) => { if (!value) throw new Error(name); checks.push(name); };
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.url().includes('/api/hr/')) apiRequests.push(r.method() + ' ' + new URL(r.url()).pathname); });
  async function variant(name) {
    await page.getByRole('navigation', { name:'Варианты меню' }).getByRole('button', { name:new RegExp(name + '$') }).click();
    await page.getByRole('region', { name:'Макет: ' + name, exact:true }).waitFor();
  }
  async function go(name) {
    const launcher = page.getByRole('button', { name:'Открыть разделы', exact:true });
    if (await launcher.isVisible()) await launcher.click();
    await page.getByRole('navigation', { name:'Разделы работодателя' }).getByRole('link', { name, exact:true }).filter({ visible:true }).click();
  }
  const overflow = async () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && [...document.querySelectorAll('.hml-content')].every(el => el.scrollWidth <= el.clientWidth + 1));
  await page.goto(url); await page.locator('.hml-app').waitFor();
  await page.setViewportSize({ width:1440, height:1000 });
  for (const [id, name] of variants) {
    await variant(name);
    await page.getByRole('button', { name:'HR Vision · задачи найма' }).click();
    await page.getByRole('heading', { name:'Задачи найма', exact:true }).waitFor();
    check(new URL(page.url()).searchParams.get('menu') === id, id + ': shareable variant URL');
    check(await overflow(), id + ': desktop fits');
    await page.screenshot({ path:'output/playwright/menu-' + id + '-desktop.png' });
    await page.locator('.hml-task-card').click();
    await page.getByRole('button', { name:/Михаил Белов 4 года/ }).click();
    await page.getByRole('button', { name:'Опыт', exact:true }).click();
    await variant(name === 'Верхняя строка' ? 'Боковое меню' : 'Верхняя строка');
    check(await page.getByRole('heading', { name:'Михаил Белов', exact:true }).isVisible(), id + ': candidate survives comparison');
    check(await page.getByRole('button', { name:'Опыт', exact:true }).getAttribute('aria-pressed') === 'true', id + ': material tab survives comparison');
    await variant(name);
    await go('Встречи');
    await page.getByRole('button', { name:'Открыть встречу', exact:true }).click();
    await page.getByRole('dialog').waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({ state:'hidden' });
    await page.waitForFunction(() => document.activeElement?.matches('.hml-meeting-actions .hml-primary'));
    check(await page.getByRole('button', { name:'Открыть встречу', exact:true }).evaluate(el => el === document.activeElement), id + ': meeting Escape restores focus');
    await page.getByRole('button', { name:'Открыть встречу', exact:true }).click();
    await page.getByRole('dialog').getByRole('button', { name:'Материалы кандидата', exact:true }).click();
    await page.getByRole('heading', { name:'Анна Миронова', exact:true }).waitFor();
    check(await page.getByRole('group', { name:'Режим просмотра интервью' }).isVisible(), id + ': interview remains accessible');
    await go('Чат');
    await page.getByRole('textbox', { name:'Сообщение', exact:true }).fill('Проверка меню ' + id);
    await page.getByRole('button', { name:'Добавить сообщение в макет' }).click();
    await go('Задачи'); await go('Чат');
    check(await page.getByText('Проверка меню ' + id, { exact:true }).isVisible(), id + ': draft conversation stays in preview');
  }
  await variant('Компактная панель');
  await page.getByRole('button', { name:'Раскрыть меню' }).click();
  await page.waitForFunction(() => Math.abs(document.querySelector('.hml-rail').getBoundingClientRect().width - 208) < 1);
  await page.getByRole('navigation', { name:'Разделы работодателя' }).getByRole('link', { name:'Встречи' }).focus();
  await page.keyboard.press('Enter');
  check(await page.getByRole('link', { name:'Встречи', exact:true }).evaluate(el => el === document.activeElement), 'Rail selection preserves focused link while collapsing');
  await page.getByRole('button', { name:'Раскрыть меню' }).click();
  await page.keyboard.press('Escape');
  check(await page.getByRole('button', { name:'Раскрыть меню' }).evaluate(el => el === document.activeElement), 'Rail Escape restores toggle focus');
  for (let i = 0; i < 5; i++) await page.locator('.hml-rail-toggle').click();
  await page.waitForFunction(() => Math.abs(document.querySelector('.hml-rail').getBoundingClientRect().width - 208) < 1);
  check(true, 'Rapid rail reversal settles to latest intent');
  await variant('Меню по кнопке');
  const launcher = page.getByRole('button', { name:'Открыть разделы', exact:true });
  await launcher.focus(); await page.keyboard.press('Enter');
  await page.locator('.hml-launcher').waitFor();
  await page.screenshot({ path:'output/playwright/menu-launcher-open.png' });
  await page.keyboard.press('Escape');
  await page.locator('.hml-launcher').waitFor({ state:'hidden' });
  await page.waitForFunction(() => document.activeElement?.matches('.hml-launcher-trigger'));
  check(await launcher.evaluate(el => el === document.activeElement), 'Launcher keyboard Escape restores focus');
  await launcher.click(); await page.getByRole('heading', { name:'Меню работодателя', exact:true }).click();
  await page.locator('.hml-launcher').waitFor({ state:'hidden' });
  check(true, 'Launcher dismisses outside');
  await page.setViewportSize({ width:390, height:844 });
  for (const [id, name] of variants) {
    await variant(name);
    await page.getByRole('button', { name:'HR Vision · задачи найма' }).click();
    check(await overflow(), id + ': 390px fits');
    await page.screenshot({ path:'output/playwright/menu-' + id + '-mobile.png', fullPage:true });
    await go('Встречи');
    await page.getByRole('button', { name:'Материалы кандидата', exact:true }).click();
    check(await page.getByRole('heading', { name:'Анна Миронова', exact:true }).isVisible(), id + ': mobile materials opens profile directly');
    check(await overflow(), id + ': mobile profile fits');
    await page.getByRole('button', { name:'Кандидаты', exact:true }).click();
    check(await page.getByRole('button', { name:/Михаил Белов 4 года/ }).isVisible(), id + ': profile returns to list');
  }
  await page.setViewportSize({ width:1024, height:768 });
  for (const [id, name] of variants) { await variant(name); check(await overflow(), id + ': tablet fits'); }
  await page.emulateMedia({ reducedMotion:'reduce' });
  await page.setViewportSize({ width:1440, height:1000 });
  await variant('Компактная панель');
  await page.getByRole('button', { name:'Раскрыть меню' }).click();
  check(await page.locator('.hml-rail').evaluate(el => Math.abs(el.getBoundingClientRect().width - 208) < 1), 'Reduced motion expands immediately');
  await variant('Меню по кнопке'); await go('Задачи');
  check(await page.getByRole('heading', { name:'Задачи найма', exact:true }).isVisible(), 'Reduced motion navigation works');
  check(errors.length === 0, 'No JavaScript errors');
  check(apiRequests.length === 0, 'Gallery makes no hiring API requests');
  return { count:checks.length, checks, errors, apiRequests };
}
