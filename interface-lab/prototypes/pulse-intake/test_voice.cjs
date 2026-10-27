const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const sandbox = {module:{exports:{}}, Blob, AbortController, Date, setTimeout, clearTimeout, setInterval, clearInterval};
vm.runInNewContext(fs.readFileSync('./web/voice.js','utf8'),sandbox);
const VoiceInput = sandbox.module.exports;
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup(overrides = {}) {
  let stopped = 0, uploaded = 0;
  const states = [], results = [], errors = [];
  const stream = {getTracks: () => [{stop: () => stopped++}]};
  class Recorder {
    static isTypeSupported(type) {return type === 'audio/mp4';}
    constructor(stream, options) {this.mimeType = options.mimeType; this.state = 'inactive';}
    start() {this.state = 'recording';}
    stop() {this.state = 'inactive'; setImmediate(() => {this.ondataavailable({data:new Blob(['x'.repeat(200)], {type:this.mimeType})}); this.onstop();});}
  }
  const voice = new VoiceInput({
    mediaDevices:{getUserMedia:async () => stream}, Recorder,
    upload: async blob => {uploaded++; assert.equal(blob.type,'audio/mp4'); return 'Нужны встречи';},
    onState:s=>states.push(s),onResult:t=>results.push(t),onError:e=>errors.push(e), ...overrides
  });
  return {voice, states, results, errors, stream, stopped:()=>stopped, uploaded:()=>uploaded};
}
test('recording is opt-in, stops microphone and returns editable transcript only', async () => {
  const t = setup(); assert.equal(t.voice.state,'idle'); assert.equal(t.uploaded(),0);
  await t.voice.start(); assert.equal(t.voice.state,'recording');
  t.voice.stop(); assert.equal(t.stopped(),1);
  await tick(); await tick();
  assert.deepEqual(t.results,['Нужны встречи']); assert.equal(t.voice.state,'idle'); assert.equal(t.uploaded(),1);
});
test('cancel discards recording and never uploads audio', async () => {
  const t=setup(); await t.voice.start(); t.voice.cancel(); await tick();
  assert.equal(t.uploaded(),0); assert.equal(t.stopped(),1); assert.deepEqual(t.results,[]);
});
test('late permission result is released after cancel', async () => {
  let grant; const t=setup({mediaDevices:{getUserMedia:()=>new Promise(resolve=>grant=resolve)}});
  const pending=t.voice.start(); t.voice.cancel(); grant(t.stream); await pending;
  assert.equal(t.stopped(),1); assert.equal(t.voice.state,'idle'); assert.equal(t.uploaded(),0);
});
test('permission denial is recoverable', async () => {
  const error = new Error(); error.name='NotAllowedError';
  const t=setup({mediaDevices:{getUserMedia:async()=>{throw error;}}});
  await t.voice.start(); assert.equal(t.voice.state,'idle'); assert.match(t.errors[0],/Разрешите/);
});
test('cancel during transcription does not append late text', async () => {
  let finish; const t=setup({upload:()=>new Promise(resolve=>finish=resolve)});
  await t.voice.start(); t.voice.stop(); await tick(); t.voice.cancel(); finish('late'); await tick();
  assert.deepEqual(t.results,[]); assert.equal(t.voice.state,'idle');
});
test('a cancelled recorder cannot stop a newer recording', async () => {
  const t=setup(); await t.voice.start(); t.voice.cancel(); await t.voice.start(); await tick();
  assert.equal(t.stopped(),1); assert.equal(t.voice.state,'recording'); t.voice.cancel();
});
