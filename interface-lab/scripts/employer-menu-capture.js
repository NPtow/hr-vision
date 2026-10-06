// Settled visual states, public deployment only; no requests to hiring API.
async page => {
  await page.emulateMedia({ reducedMotion:'no-preference' });
  await page.goto('https://hr-vision.158-160-179-53.sslip.io/iframe.html?id=hr-vision-employer-menu--gallery&viewMode=story&menu=top');
  const variants = [['sidebar','Боковое меню'],['rail','Компактная панель'],['top','Верхняя строка'],['dock','Плавающее меню'],['launcher','Меню по кнопке']];
  async function choose(name) { await page.getByRole('navigation', { name:'Варианты меню' }).getByRole('button', { name:new RegExp(name + '$') }).click(); }
  async function settled() { await page.waitForFunction(() => [...document.querySelectorAll('.hml-active-mark')].every(el => { const r=el.getBoundingClientRect(), p=el.parentElement.getBoundingClientRect(); return Math.abs((r.left+r.right)/2-(p.left+p.right)/2)<.25 && (Math.abs(r.top-p.top)<.25 || Math.abs(r.bottom-p.bottom)<2); })); }
  await page.setViewportSize({ width:1440, height:1000 });
  for (const [id,name] of variants) {
    await choose(name); await settled();
    await page.screenshot({ path:'output/playwright/menu-' + id + '-desktop.png' });
  }
  await page.getByRole('button', { name:'Открыть разделы' }).click();
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('.hml-launcher')).opacity)>.995);
  await page.screenshot({ path:'output/playwright/menu-launcher-open.png' });
  await page.keyboard.press('Escape');
  await choose('Компактная панель'); await page.getByRole('button', { name:'Раскрыть меню' }).click();
  await page.waitForFunction(() => Math.abs(document.querySelector('.hml-rail').getBoundingClientRect().width-208)<.25);
  await page.screenshot({ path:'output/playwright/menu-rail-open.png' });
  await choose('Верхняя строка'); await page.locator('.hml-task-card').click();
  await page.locator('.ir-review').waitFor();
  await page.screenshot({ path:'output/playwright/menu-candidate-desktop.png' });
  await page.setViewportSize({ width:390, height:844 });
  await page.getByRole('button', { name:/Анна Миронова 5 лет/ }).click();
  await page.getByRole('heading', { name:'Анна Миронова', exact:true }).waitFor();
  await page.screenshot({ path:'output/playwright/menu-candidate-mobile.png', fullPage:true });
  return {captured:9};
}
