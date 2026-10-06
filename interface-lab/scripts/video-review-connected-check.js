// Public deployed UI. Live checks are read-only; post-call API responses are isolated
// browser fixtures so the team's current interviews, offers and messages stay untouched.
async page => {
  const team = __TEAM_JSON__;
  const fixture = __STATE_JSON__;
  const videoBytes = Buffer.from(__VIDEO_BASE64_JSON__, 'base64');
  const site = 'https://hr-vision.158-160-179-53.sslip.io';
  const entry = site + '/iframe.html?id=hr-vision-product--start&viewMode=story#team=' + encodeURIComponent(team);
  const videoFile = '/Users/NIKITA/Documents/ChatGPT/HR Vision/tmp/interview-test/technical-playback.mp4';
  const checks = [], errors = [];
  const assert = (v, label) => { if(!v) throw new Error(label); checks.push(label); };
  const browser = page.context().browser();
  const live = await browser.newContext({viewport:{width:1440,height:1000}});
  const qa = await browser.newContext({viewport:{width:1440,height:1000}});
  const button = (p, name) => p.getByRole('button',{name,exact:true});
  const login = async p => {
    p.on('pageerror', e => errors.push(e.message));
    await p.goto(entry);
    await p.getByRole('button').filter({has:p.getByRole('heading',{name:'Иван Петров',exact:true})}).click();
    await button(p,'Открыть подборку').click();
  };
  try {
    const real = await live.newPage();
    await login(real);
    const token = await real.evaluate(()=>sessionStorage.getItem('hr-vision-connected-session'));
    const before = await (await real.request.get(site+'/api/hr/state',{headers:{Authorization:'Bearer '+token}})).json();
    await real.getByRole('tab',{name:'Интервью',exact:true}).waitFor();
    assert(await real.getByRole('tab',{name:'Интервью',exact:true}).getAttribute('aria-selected')==='true','Interview opens by default on live shortlist');
    assert(await real.locator('.ir-review .ir-player').isVisible(),'Interview player is visible without searching in tabs');
    const playerRect=await real.locator('.ir-player').boundingBox();
    const chaptersRect=await real.locator('.ir-materials').boundingBox();
    assert(playerRect.height<400 && chaptersRect.x>playerRect.x+playerRect.width-1,'Desktop player and moments fit side by side');
    assert(await real.getByRole('slider',{name:'Позиция видеозаписи'}).isDisabled(),'Unavailable media has no pretend timeline');
    await real.screenshot({path:'output/playwright/video-review-shortlist-live-1440.png',fullPage:true});
    // Attach only a local synthetic fixture, never upload it to the application server.
    await real.locator('.cj-test-footer summary').first().click();
    await real.locator('.ir-lab-media summary').click();
    await real.getByLabel('Локальный видеофайл',{exact:true}).setInputFiles(videoFile);
    const player = real.locator('.cj-profile video');
    await player.evaluate(v => v.readyState >= 1 ? undefined : new Promise((ok,fail)=>{v.addEventListener('loadedmetadata',ok,{once:true});v.addEventListener('error',fail,{once:true});}));
    await real.locator('.ir-chapters button').nth(1).click();
    assert(await player.evaluate(v=>Math.abs(v.currentTime-695)<.5),'Screening chapter seeks actual video to 11:35');
    await real.getByRole('slider',{name:'Позиция видеозаписи'}).focus();
    await real.keyboard.press('Home');
    assert(await player.evaluate(v=>v.currentTime<.5),'Timeline supports keyboard seek');
    await real.locator('.ir-timeline-markers button').nth(1).click();
    assert(await player.evaluate(v=>Math.abs(v.currentTime-695)<.5),'Timeline marker seeks actual video');
    await button(real,'Полное интервью').click();
    assert(await player.evaluate(v=>Math.abs(v.currentTime-695)<.5),'Changing viewing mode preserves current position');
    await button(real,'Открыть встречу').click();
    await button(real,'Первичное интервью кандидата').click();
    assert(await real.getByRole('tab',{name:'Интервью',exact:true}).getAttribute('aria-selected')==='true','Meeting returns directly to original interview');
    const after = await (await real.request.get(site+'/api/hr/state',{headers:{Authorization:'Bearer '+token}})).json();
    assert(after.revision === before.revision && after.generation === before.generation,'Live scenario was not mutated by media checks');

    // The remainder uses synthetic server responses only in this isolated QA browser.
    const requests=[];
    await qa.route('**/api/hr/**', async route => {
      const name = new URL(route.request().url()).pathname.split('/').pop();
      requests.push(name);
      if (name === 'access') return route.fulfill({ json: { ready: true } });
      if(name==='session') return route.fulfill({json:{token:'isolated-browser-fixture'}});
      if(name==='state') return route.fulfill({json:fixture});
      if(name==='materials') {
        const body=route.request().postDataJSON();
        const anna = body.candidate === 'anna';
        return route.fulfill({json:{recordings:anna?[{id:'qa-video',url:site+'/__qa__/recording.mp4'}]:[],transcripts:anna?[{id:'qa-transcript',text:'WEBVTT\n\n00:00:04.000 --> 00:00:08.000\n<v Анна Миронова>Тестовая реплика после встречи.</v>\n\n00:00:12.000 --> 00:00:17.000\n<v Иван Петров>Тестовый вопрос по результату.</v>\n'}]:[]}});
      }
      return route.fulfill({status:405,json:{error:'This regression run is read-only'}});
    });
    await qa.route('**/__qa__/recording.mp4', route => {
      const range=route.request().headers().range?.match(/bytes=(\d+)-(\d*)/);
      if(!range) return route.fulfill({body:videoBytes,contentType:'video/mp4',headers:{'Accept-Ranges':'bytes'}});
      const start=Number(range[1]), end=range[2] ? Math.min(Number(range[2]),videoBytes.length-1) : videoBytes.length-1;
      return route.fulfill({status:206,body:videoBytes.subarray(start,end+1),contentType:'video/mp4',headers:{'Accept-Ranges':'bytes','Content-Range':`bytes ${start}-${end}/${videoBytes.length}`}});
    });
    const p=await qa.newPage();
    await login(p);
    await p.getByRole('navigation',{name:'Этапы найма'}).getByRole('button',{name:'Разбор встречи',exact:true}).click();
    await p.getByRole('heading',{name:'Запись вашей встречи',exact:true}).waitFor();
    const meetingVideo=p.locator('.cj-review-layout video');
    await meetingVideo.evaluate(v => v.readyState >= 1 ? undefined : new Promise((ok,fail)=>{v.addEventListener('loadedmetadata',ok,{once:true});v.addEventListener('error',fail,{once:true});}));
    assert(requests.includes('materials'),'Meeting materials are requested automatically');
    await p.locator('.ir-chapters button').nth(1).click();
    const seekInfo=await meetingVideo.evaluate(v=>({time:v.currentTime,duration:v.duration,ready:v.readyState,seeking:v.seeking}));
    if(Math.abs(seekInfo.time-12)>=.5) throw new Error('Meeting seek failed: '+JSON.stringify(seekInfo));
    assert(true,'WebVTT fragment seeks meeting recording to its own timestamp');
    assert((await p.locator('.ir-excerpt').innerText()).includes('Тестовый вопрос'),'Meeting transcript displays matching text');
    assert(!(await p.locator('.cj-review-layout').innerText()).includes('Возвращала неактивных клиентов'),'Screening evidence is not passed off as meeting material');
    await p.getByRole('tab',{name:'Оценка интервью',exact:true}).click();
    assert(await p.getByText('Заглушка · анализ не подключён',{exact:true}).isVisible(),'Assessment explicitly retains agreed placeholder boundary');
    await p.getByRole('tab',{name:'Ваш фидбек',exact:true}).click();
    assert(await p.getByText('Тест: показал пример по задаче',{exact:true}).isVisible(),'Employer feedback remains next to the recording');
    for(const width of [1440,390]) {
      await p.setViewportSize({width,height:1000});
      assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`Review fits ${width}px`);
      await p.screenshot({path:`output/playwright/video-review-meeting-fixture-${width}.png`,fullPage:true});
    }
    await p.setViewportSize({width:1440,height:1000});
    await p.getByRole('navigation',{name:'Этапы найма'}).getByRole('button',{name:'Подборка · 3',exact:true}).click();
    await p.getByRole('button',{name:/Михаил Белов/}).first().click();
    await p.getByRole('navigation',{name:'Этапы найма'}).getByRole('button',{name:'Разбор встречи',exact:true}).click();
    await p.getByRole('heading',{name:'Запись вашей встречи',exact:true}).waitFor();
    assert(await p.locator('.cj-review-layout video').count()===0,'Switching candidate removes previous meeting recording');
    assert(!(await p.locator('.cj-review-layout').innerText()).includes('Тестовая реплика'),'Switching candidate removes previous meeting transcript');
    assert(errors.length===0,'No browser runtime errors');
    return {status:'passed',count:checks.length,checks,errors,postCallVerification:'isolated browser fixtures; real provider not configured'};
  } finally {await live.close();await qa.close();}
}
