// Public HTTPS UI + real Daily call; isolated business state. No team reset.
// Materialize placeholders in ignored tmp. Never commit provider tokens.
async page => {
  const qa = __PRIVATE_QA__;
  const speech = __SPEECH__;
  const states = qa.states;
  const checks = [];
  const errors = [];
  const captured = [];
  const assert = (value, message) => { if (!value) throw new Error(message); checks.push(message); };
  const browser = page.context().browser();
  const pages = {};
  const contexts = [];
  const site = 'https://hr-vision.158-160-179-53.sslip.io';
  let failDraft = false;
  function update(fn) { Object.values(states).forEach(s => fn(s.candidates.anna)); }
  for (const actor of ['manager', 'anna']) {
    const ctx = await browser.newContext({ permissions: ['camera','microphone'], viewport: { width:1440,height:1000 } });
    contexts.push(ctx);
    await ctx.addInitScript(({ speech, actor }) => {
      const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = async constraints => {
        if (!constraints?.audio || actor !== 'manager') return original(constraints);
        const ac = new AudioContext(); await ac.resume();
        const source = ac.createBufferSource();
        source.buffer = await ac.decodeAudioData(Uint8Array.from(atob(speech),c=>c.charCodeAt(0)).buffer);
        source.loop=true; const destination=ac.createMediaStreamDestination(); source.connect(destination);source.start();
        const stream = constraints.video ? await original({video:constraints.video,audio:false}) : new MediaStream();
        const track=destination.stream.getAudioTracks()[0];stream.addTrack(track);
        track.addEventListener('ended',()=>{source.stop();void ac.close();});return stream;
      };
    },{speech,actor});
    await ctx.route('**/api/hr/**',async route => {
      const endpoint=new URL(route.request().url()).pathname.replace('/api/hr/','');
      if (endpoint === 'access') return route.fulfill({ json: { ready: true } });
      if(endpoint==='session')return route.fulfill({json:{token:'qa-'+actor}});
      if(endpoint==='state')return route.fulfill({json:states[actor]});
      if(endpoint==='room')return route.fulfill({json:{url:qa.room.url,token:qa.tokens[actor]}});
      if(endpoint==='materials')return route.fulfill({json:{recordings:[],transcripts:[]}});
      if(endpoint==='feedback/questions')return route.fulfill({json:{status:states.manager.candidates.anna.meeting.questionnaire?'ready':'processing',questionnaire:states.manager.candidates.anna.meeting.questionnaire,draft:states.manager.candidates.anna.meeting.feedbackDraft||{}}});
      if(endpoint==='action') {
        const b=route.request().postDataJSON();const p=states.manager.candidates.anna;
        if(b.action==='meeting.joined')update(x=>{x.meeting.status='live';if(!x.meeting.joined.includes(actor))x.meeting.joined.push(actor);});
        else if(b.action==='meeting.transcript')captured.push(...b.turns);
        else if(b.action==='meeting.finish') {
          update(x=>{x.meeting.status='completed';x.meeting.endedAt=new Date().toISOString();});
          const unique=[...new Map(captured.filter(t=>/\?|^(Как|Какие|Расскажите)/i.test(t.text)).map(t=>[t.text,t])).values()].slice(0,3);
          p.meeting.questionnaire={id:'real-speech-qa',meetingId:p.meeting.id,questions:unique.map((t,i)=>({id:'q'+i,question:t.text,seconds:t.seconds,source:'live'}))};
        } else if(b.action==='feedback.draft') {
          if(failDraft)return route.fulfill({status:503,json:{error:'QA: temporary outage'}});
          p.meeting.feedbackDraft=b.answers;
        } else if(b.action==='feedback')p.feedback={questions:p.meeting.questionnaire.questions,answers:b.answers,at:new Date().toISOString()};
        else if(b.action==='feedback.questions')p.meeting.questionnaire={id:'manual',meetingId:p.meeting.id,questions:b.questions.split('\n').filter(Boolean).map((question,i)=>({id:'m'+i,question,seconds:null,source:'manual'}))};
        else if(b.action==='after')update(x=>{x.afterInterest=b.value;});
        else return route.fulfill({status:409,json:{error:'Unimplemented isolated QA action'}});
        return route.fulfill({json:states[actor]});
      }
      return route.fulfill({status:405,json:{error:'QA endpoint missing'}});
    });
    const p=await ctx.newPage();pages[actor]=p;p.on('pageerror',e=>errors.push(e.message));
    await p.goto(site+'/iframe.html?id=hr-vision-product--start&viewMode=story#team=isolated-feedback-qa');
    await p.getByRole('button').filter({has:p.getByRole('heading',{name:actor==='manager'?'Иван Петров':'Анна Миронова',exact:true})}).click();
    if(actor==='manager')await p.getByRole('button',{name:'Открыть подборку',exact:true}).click();
    await p.getByRole('button',{name:'Открыть встречу',exact:true}).click();
    assert(await p.getByRole('button',{name:/фидбек/i}).count()===0,actor+': no feedback before completion');
    await p.getByRole('checkbox',{name:/Согласен на запись/}).check();
    await p.getByRole('button',{name:actor==='manager'?'Войти в видеовстречу':'Вернуться во встречу',exact:true}).click();
    await p.locator('.cj-room iframe').waitFor();
    const frame=p.frameLocator('.cj-room iframe');
    await frame.getByRole('button',{name:'Join',exact:true}).click();
    await p.waitForFunction(()=>document.querySelector('.cj-call-bar')?.textContent?.includes('Ждём собеседника')||document.querySelector('.cj-call-bar')?.textContent?.includes('Встреча'));
    const bounds=await p.locator('.cj-call-overlay').boundingBox();
    assert(bounds.x===0&&bounds.y===0&&bounds.width===1440&&bounds.height===1000,actor+': call occupies entire viewport');
    assert(await p.locator('.cj-app[inert][aria-hidden=true]').count()===1,actor+': underlying app inaccessible during call');
    await p.screenshot({path:'output/playwright/feedback-'+actor+'-call-1440.png'});
  }
  const manager=pages.manager,candidate=pages.anna;
  await candidate.setViewportSize({width:390,height:844});
  await candidate.screenshot({path:'output/playwright/feedback-candidate-call-390.png'});
  assert((await candidate.locator('.cj-call-overlay').boundingBox()).width===390,'Candidate call fits 390px');
  await new Promise((resolve,reject)=>{const started=Date.now();const timer=setInterval(()=>{if(captured.length>=3){clearInterval(timer);resolve();}else if(Date.now()-started>55000){clearInterval(timer);reject(new Error('No live manager transcription captured'));}},500);});
  assert(captured.some(t=>/клиент/i.test(t.text)),'Actual Daily manager speech reaches transcription capture');
  for (const [actor,p] of Object.entries(pages)) {
    const frame=p.frameLocator('.cj-room iframe');
    await frame.locator('video').first().waitFor({state:'visible'});
    assert(await frame.locator('video').count()>=2,actor+': both video tiles visible');
    await p.screenshot({path:'output/playwright/feedback-'+actor+'-call-ready.png'});
  }
  await manager.getByRole('button',{name:'Завершить встречу',exact:true}).click();
  await manager.getByRole('heading',{name:'Зафиксируем итоги встречи'}).waitFor();
  assert(await manager.locator('.cj-call-overlay').count()===0,'Manager call closes into mandatory feedback');
  assert(await manager.locator('.cj-nav').count()===0,'No navigation around required feedback');
  assert(await manager.locator('.cj-feedback-card blockquote').innerText()===states.manager.candidates.anna.meeting.questionnaire.questions[0].question,'Feedback shows the actual captured question');
  assert(await manager.getByRole('heading',{name:'Зафиксируем итоги встречи'}).evaluate(e=>document.activeElement===e),'Focus moves to post-call heading');
  await candidate.waitForFunction(()=>!document.querySelector('.cj-call-overlay'),{timeout:15000});
  assert(await candidate.getByRole('heading',{name:'Хотите продолжить с этой компанией?'}).isVisible(),'Remote completion exits candidate call to candidate next step');
  assert(await candidate.locator('.cj-feedback-stage').count()===0,'Employer questionnaire not exposed to candidate');
  await manager.getByRole('radio',{name:'Убедительный ответ',exact:true}).check();
  await manager.getByRole('textbox',{name:/Пояснение/}).fill('QA: подтверждён пример работы с клиентами');
  await manager.getByText('Черновик сохранён',{exact:true}).waitFor();
  await manager.reload();
  await manager.getByRole('button').filter({has:manager.getByRole('heading',{name:'Иван Петров',exact:true})}).click();
  await manager.getByRole('heading',{name:'Зафиксируем итоги встречи'}).waitFor();
  assert(await manager.getByRole('textbox',{name:/Пояснение/}).inputValue()==='QA: подтверждён пример работы с клиентами','Draft restored after reload and login');
  await manager.setViewportSize({width:390,height:844});
  assert(await manager.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Feedback has no 390px horizontal overflow');
  await manager.screenshot({path:'output/playwright/feedback-form-390.png',fullPage:true});
  await manager.setViewportSize({width:1440,height:1000});
  await manager.screenshot({path:'output/playwright/feedback-form-1440.png',fullPage:true});
  const questions=states.manager.candidates.anna.meeting.questionnaire.questions;
  for(let i=0;i<questions.length;i++){
    await manager.getByText(questions[i].question,{exact:true}).waitFor();
    if(i)await manager.getByRole('radio',{name:'Нужно уточнить',exact:true}).check();
    await manager.getByRole('button',{name:i===questions.length-1?'Сохранить итоги':'Далее',exact:true}).click();
  }
  await manager.getByRole('heading',{name:'Разбор встречи',exact:true}).waitFor();
  assert(Boolean(states.manager.candidates.anna.feedback),'All dynamic answers submitted before review');
  assert(await manager.getByRole('heading',{name:'Запись вашей встречи',exact:true}).isVisible(),'Video review remains available after feedback');
  // A second completed interview cannot hide behind the currently selected person.
  const second=structuredClone(states.manager.candidates.anna);second.id='mikhail';second.name='Михаил Белов';second.feedback=null;second.meeting.id='second-qa';second.meeting.questionnaire={id:'second-q',meetingId:'second-qa',questions:[{id:'b',question:'Какие показатели вы считали?',seconds:30,source:'transcript'}]};
  states.manager.candidates.mikhail=second;
  await manager.getByText('Михаил Белов · встреча завершена',{exact:true}).waitFor({timeout:15000});
  assert(await manager.getByRole('heading',{name:'Зафиксируем итоги встречи'}).isVisible(),'Pending feedback queue covers other candidates');
  delete states.manager.candidates.mikhail;
  // Missing transcript is shown honestly and permits own recalled questions.
  states.manager.candidates.anna.feedback=null;states.manager.candidates.anna.meeting.id='missing-qa';states.manager.candidates.anna.meeting.questionnaire=null;
  await manager.getByText('Расшифровка обрабатывается. Вопросы появятся здесь автоматически.',{exact:true}).waitFor({timeout:15000});
  assert(await manager.locator('.cj-feedback-card blockquote').count()===0,'Missing transcript does not substitute fixed questions');
  await manager.getByText('Записать заданные вопросы вручную',{exact:true}).click();
  await manager.getByRole('textbox',{name:'Вопросы, заданные на встрече'}).fill('Как вы согласовали бюджет с клиентом?');
  await manager.getByRole('button',{name:'Продолжить по этим вопросам'}).click();
  await manager.locator('.cj-feedback-card blockquote').filter({hasText:'Как вы согласовали бюджет с клиентом?'}).waitFor();
  await manager.getByText('Указан вами',{exact:true}).waitFor();
  assert(await manager.getByText('Указан вами',{exact:true}).isVisible(),'Manual recovery is explicit and question-specific');
  assert(errors.length===0,'No browser JavaScript exceptions');
  return {status:'passed',checks,questions:questions.map(q=>q.question),capturedTurns:captured.length,errors,boundary:'Published UI with real Daily media and synthetic Russian speech; business actions isolated, server separately covered by 34 tests'};
}
