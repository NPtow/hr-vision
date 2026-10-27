import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { CallConnectionError, DAILY_BUNDLE_VERSION, PreviewTimeoutError, dailyBundleDirectory, dailyCallBundleConfig, deviceAccessMessage, directCallUrl, releaseCall, waitForCall, waitForPreview } from '../src/scenes/candidate-portal/call-connection.ts';
import type { DailyParticipant, DailyTrackState } from '@daily-co/daily-js';
import { callInputTracks, callLayout, canSendTrack, mediaTrack } from '../src/scenes/candidate-portal/call-media.ts';

test('the call engine is loaded from our origin, not Daily CloudFront', () => {
  const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  assert.equal(DAILY_BUNDLE_VERSION, pkg.dependencies['@daily-co/daily-js']);
  const directory = dailyBundleDirectory('https://mashadasha.tech/iframe.html?id=hr-vision-product--manager&viewMode=story');
  assert.equal(directory, `https://mashadasha.tech/daily-call/${DAILY_BUNDLE_VERSION}/`);
  assert.equal(directory.includes('daily.co'), false);
  assert.deepEqual(dailyCallBundleConfig('https://mashadasha.tech/iframe.html'), { dailyConfig: { bundlePathOverride: directory } });
});

test('a call that never resolves can be cancelled without waiting for the provider', async () => {
  const controller = new AbortController();
  const pending = waitForCall(new Promise<void>(() => {}), controller.signal);
  const reason = new CallConnectionError('connection deadline');
  controller.abort(reason);
  await assert.rejects(pending, error => error === reason);
});

test('late provider success cannot revive a cancelled connection', async () => {
  const controller = new AbortController();
  let finish!: (value: string) => void;
  let connected = false;
  const pending = waitForCall(new Promise<string>(resolve => { finish = resolve; }), controller.signal);
  pending.then(() => { connected = true; }, () => {});
  controller.abort(new Error('left'));
  finish('joined');
  await assert.rejects(pending, /left/);
  assert.equal(connected, false);
});

test('a successful connection returns its result and is unaffected by later abort', async () => {
  const controller = new AbortController();
  assert.equal(await waitForCall(Promise.resolve('joined'), controller.signal), 'joined');
  controller.abort();
});

test('already cancelled attempts consume late provider rejection', async () => {
  const controller = new AbortController();
  controller.abort(new Error('unmounted'));
  await assert.rejects(waitForCall(Promise.reject('SDK load failed'), controller.signal), /unmounted/);
});

test('an unresponsive iframe cannot block closing indefinitely', async () => {
  let detached = false;
  await assert.rejects(releaseCall({
    destroy: () => new Promise<void>(() => {}),
    iframe: () => ({ remove: () => { detached = true; } }),
  }, 5), CallConnectionError);
  assert.equal(detached, true, 'a late CDN recovery must not start devices behind the error screen');
});

test('normal frame cleanup and rejected cleanup both settle', async () => {
  await releaseCall({ destroy: () => Promise.resolve() }, 100);
  await assert.rejects(releaseCall({ destroy: () => Promise.reject(new Error('iframe gone')) }, 100), /iframe gone/);
});

test('an unanswered device permission prompt stops blocking entry', async () => {
  await assert.rejects(waitForPreview(new Promise<MediaStream>(() => {}), new AbortController().signal, 5), PreviewTimeoutError);
});

test('camera granted after the preview timed out is immediately released', async () => {
  let grant!: (stream: { getTracks: () => { stop: () => void }[] }) => void;
  let stopped = 0;
  const operation = new Promise<{ getTracks: () => { stop: () => void }[] }>(resolve => { grant = resolve; });
  await assert.rejects(waitForPreview(operation, new AbortController().signal, 5), PreviewTimeoutError);
  grant({ getTracks: () => [{ stop: () => { stopped++; } }] });
  await operation;
  await Promise.resolve();
  assert.equal(stopped, 1);
});

test('leaving preview before permission is granted releases late devices', async () => {
  const controller = new AbortController();
  let grant!: (stream: { getTracks: () => { stop: () => void }[] }) => void;
  let stopped = false;
  const operation = new Promise<{ getTracks: () => { stop: () => void }[] }>(resolve => { grant = resolve; });
  const preview = waitForPreview(operation, controller.signal);
  controller.abort(new Error('left preview'));
  await assert.rejects(preview, /left preview/);
  grant({ getTracks: () => [{ stop: () => { stopped = true; } }] });
  await operation;
  await Promise.resolve();
  assert.equal(stopped, true);
});

test('normal preview keeps the granted devices and preserves permission errors', async () => {
  let stopped = false;
  const media = { getTracks: () => [{ stop: () => { stopped = true; } }] };
  assert.equal(await waitForPreview(Promise.resolve(media), new AbortController().signal), media);
  assert.equal(stopped, false);
  const denied = new DOMException('denied', 'NotAllowedError');
  await assert.rejects(waitForPreview(Promise.reject(denied), new AbortController().signal), error => error === denied);
});

test('fallback preserves the exact private room and encodes its participant token', () => {
  const link = new URL(directCallUrl({ url: 'https://team.daily.co/interview', token: 'test-only.a+b/c=' }));
  assert.equal(link.origin + link.pathname, 'https://team.daily.co/interview');
  assert.equal(link.searchParams.get('t'), 'test-only.a+b/c=');
});

test('fallback cannot send a participant token to another origin', () => {
  for (const url of ['https://evil.test/room', 'https://daily.co.evil.test/room', 'http://team.daily.co/room', 'https://user@team.daily.co/room']) {
    assert.throws(() => directCallUrl({ url, token: 'test-only' }), CallConnectionError);
  }
});

test('device denial, missing hardware, busy hardware and unanswered prompts give distinct recovery steps', () => {
  assert.match(deviceAccessMessage(new DOMException('', 'NotAllowedError')), /настройках сайта/);
  assert.match(deviceAccessMessage(new DOMException('', 'NotFoundError')), /не найдены/);
  assert.match(deviceAccessMessage(new DOMException('', 'NotReadableError')), /другой звонок/);
  assert.match(deviceAccessMessage(new PreviewTimeoutError()), /не ответил/);
  assert.doesNotMatch(deviceAccessMessage(new Error('secret provider detail')), /secret provider detail/);
});

function person(id: string, local = false, sharing = false): DailyParticipant {
  const off: DailyTrackState = { state: 'off', subscribed: true };
  return { session_id: id, user_name: id, local, tracks: { audio: off, video: off, screenAudio: off,
    screenVideo: { ...off, state: sharing ? 'playable' : 'off' } }, permissions: { canSend: true } } as DailyParticipant;
}

test('candidate call keeps self out of remote audio and preserves every remote participant', () => {
  const me = person('candidate', true), recruiter = person('recruiter'), guest = person('guest');
  const layout = callLayout([recruiter, me, guest]);
  assert.equal(layout.local, me);
  assert.deepEqual(layout.remote, [recruiter, guest]);
  assert.equal(layout.sharing, undefined);
  assert.deepEqual(callLayout([]), { local: undefined, remote: [], sharing: undefined });
});

test('remote shared screen takes priority and falls back when sharing stops', () => {
  const me = person('candidate', true, true), recruiter = person('recruiter', false, true);
  assert.equal(callLayout([me, recruiter]).sharing, recruiter);
  recruiter.tracks.screenVideo.state = 'off';
  assert.equal(callLayout([me, recruiter]).sharing, me);
  me.tracks.screenVideo.state = 'off';
  assert.equal(callLayout([me, recruiter]).sharing, undefined);
});

test('candidate controls follow token permissions instead of granting publication privileges', () => {
  const me = person('candidate', true);
  assert.equal(canSendTrack(undefined, 'audio'), false);
  assert.equal(canSendTrack(me, 'audio'), true);
  me.permissions.canSend = new Set(['audio', 'video']);
  assert.equal(canSendTrack(me, 'audio'), true);
  assert.equal(canSendTrack(me, 'screenVideo'), false);
  me.permissions.canSend = false;
  assert.equal(canSendTrack(me, 'video'), false);
});

test('persistent track survives mute/unmute without being stopped by presentation', () => {
  let stopped = false;
  const persistent = { stop: () => { stopped = true; } } as MediaStreamTrack;
  const transient = {} as MediaStreamTrack;
  const track: DailyTrackState = { state: 'playable', subscribed: true, persistentTrack: persistent, track: transient };
  assert.equal(mediaTrack(track), persistent);
  track.state = 'off';
  assert.equal(mediaTrack(track), persistent);
  delete track.persistentTrack;
  assert.equal(mediaTrack(track), transient);
  assert.equal(mediaTrack(undefined), undefined);
  assert.equal(stopped, false);
});

test('call takes the granted preview source without recapturing devices', () => {
  const stopped: string[] = [];
  const track = (kind: string) => ({ kind, readyState: 'live', stop: () => stopped.push(`preview:${kind}`),
    clone: () => ({ kind, stop: () => stopped.push(`call:${kind}`) }) }) as MediaStreamTrack;
  const video = track('video'), audio = track('audio');
  const source = { getTracks: () => [video, audio] } as MediaStream;
  const inputs = callInputTracks(source, { camera: true, microphone: true });
  assert.notEqual(inputs.videoSource, video);
  assert.notEqual(inputs.audioSource, audio);
  assert.equal(inputs.owned.length, 2);
  inputs.owned.forEach(value => value.stop());
  assert.deepEqual(stopped, ['call:video', 'call:audio']);
});

test('audio-only and device-free entry never acquire an unrequested camera', () => {
  const audio = { kind: 'audio', readyState: 'live', clone: () => ({ kind: 'audio' }) } as MediaStreamTrack;
  const inputs = callInputTracks({ getTracks: () => [audio] } as MediaStream, { camera: false, microphone: true });
  assert.equal(inputs.videoSource, false);
  assert.equal(inputs.owned.length, 1);
  assert.deepEqual(callInputTracks(null, { camera: false, microphone: false }), { audioSource: false, videoSource: false, owned: [] });
});

test('lost preview access fails visibly and releases a partial handoff', () => {
  let stopped = false;
  const video = { kind: 'video', readyState: 'live', clone: () => ({ stop: () => { stopped = true; } }) } as unknown as MediaStreamTrack;
  assert.throws(() => callInputTracks({ getTracks: () => [video] } as MediaStream, { camera: true, microphone: true }), CallConnectionError);
  assert.equal(stopped, true);
  assert.throws(() => callInputTracks(null, { camera: false, microphone: true }), CallConnectionError);
});
