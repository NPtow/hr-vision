// Real Daily media inside the deployed UI; only journey state is an isolated fixture.
// Materialize placeholders in ignored tmp, never put credentials in this source.
async page => {
  const qa = __QA_JSON__;
  const states = __STATES_JSON__;
  const speech = __AUDIO_JSON__;
  const errors = [];
  const browser = page.context().browser();
  const site = 'https://hr-vision.158-160-179-53.sslip.io';
  const contexts = [];
  const pages = [];
  for (const actor of ['manager', 'anna']) {
    const ctx = await browser.newContext({ permissions: ['camera', 'microphone'], viewport: { width: 1440, height: 1100 } });
    contexts.push(ctx);
    // Synthetic spoken Russian, never the user's microphone. Chromium's fake-file
    // device can deliver silence on macOS, so supply a measurable Web Audio track.
    await ctx.addInitScript(({ speech }) => {
      const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = async constraints => {
        if (!constraints?.audio) return original(constraints);
        const ac = new AudioContext();
        await ac.resume();
        const bytes = Uint8Array.from(atob(speech), c => c.charCodeAt(0));
        const source = ac.createBufferSource();
        source.buffer = await ac.decodeAudioData(bytes.buffer);
        source.loop = true;
        const destination = ac.createMediaStreamDestination();
        source.connect(destination); source.start();
        const stream = constraints.video ? await original({ video: constraints.video, audio: false }) : new MediaStream();
        const track = destination.stream.getAudioTracks()[0];
        stream.addTrack(track);
        track.addEventListener('ended', () => { source.stop(); void ac.close(); });
        return stream;
      };
    }, { speech });
    await ctx.route('**/api/hr/**', async route => {
      const endpoint = new URL(route.request().url()).pathname.split('/').pop();
      if (endpoint === 'access') return route.fulfill({ json: { ready: true } });
      if (endpoint === 'session') return route.fulfill({ json: { token: 'isolated-' + actor } });
      if (endpoint === 'state') return route.fulfill({ json: states[actor] });
      if (endpoint === 'room') return route.fulfill({ json: { url: qa.room.url, token: qa.tokens[actor] } });
      if (endpoint === 'action') {
        const body = route.request().postDataJSON();
        if (body.action !== 'meeting.joined') return route.fulfill({ status: 409, json: { error: 'QA only: end the provider call separately.' } });
        for (const state of Object.values(states)) {
          state.candidates.anna.meeting.status = 'live';
          if (!state.candidates.anna.meeting.joined.includes(actor)) state.candidates.anna.meeting.joined.push(actor);
        }
        return route.fulfill({ json: states[actor] });
      }
      return route.fulfill({ status: 405, json: { error: 'QA route not provided.' } });
    });
    const p = await ctx.newPage();
    p.on('pageerror', e => errors.push(e.message));
    pages.push(p);
    await p.goto(site + '/iframe.html?id=hr-vision-product--start&viewMode=story#team=isolated-media-qa');
    const name = actor === 'manager' ? 'Иван Петров' : 'Анна Миронова';
    await p.getByRole('button').filter({ has: p.getByRole('heading', { name, exact: true }) }).click();
    if (actor === 'manager') await p.getByRole('button', { name: 'Открыть подборку', exact: true }).click();
    await p.getByRole('button', { name: 'Открыть встречу', exact: true }).click();
    await p.getByRole('checkbox', { name: /Согласен на запись/ }).check();
    await p.getByRole('button', { name: 'Войти в видеовстречу', exact: true }).click();
    await p.locator('.cj-room iframe').waitFor();
    const frame = p.frameLocator('.cj-room iframe');
    await frame.getByRole('button', { name: 'Join', exact: true }).click();
    await p.screenshot({ path: `output/playwright/daily-${actor}-prejoin.png`, fullPage: true });
  }
  return { openedFrames: pages.length, errors, boundary: 'Real Daily rooms; isolated journey fixture; synthetic camera/microphone' };
}
