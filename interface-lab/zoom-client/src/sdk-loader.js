const BASE = '/call/zoom/';
const VERSION = '6.10.0';

async function resource(name, signal) {
  // Only this release's relative paths may be fetched, never credentials or URLs from messages.
  if (!/^(assets\/[-\w.]+\.js|vendor\/6\.10\.0\/lib\/[\w./-]+\.(js|json|wasm))$/.test(name) || name.includes('..')) throw new Error('sdk_assets');
  const response = await fetch(BASE + name, { signal, cache: 'force-cache', credentials: 'same-origin' });
  if (!response.ok || /text\/html/i.test(response.headers.get('content-type') || '')) throw new Error('sdk_assets');
  const bytes = await response.arrayBuffer();
  if (name.endsWith('.wasm') && !WebAssembly.validate(bytes)) throw new Error('sdk_assets');
}

// Standard feature probes for the audio/video variants in the pinned SDK 6.10.0.
// Fetch success and WASM validation are the gate, not the SDK's early-resolving preload promise.
function wasmAssets() {
  const simd = WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,10,9,1,7,0,65,0,253,15,26,11]));
  const threads = typeof SharedArrayBuffer === 'function' && WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,5,4,1,3,1,1,10,11,1,9,0,65,0,254,16,2,0,26,11]));
  return [simd ? 'audio.simd.wasm' : 'audio.encode.wasm',
    threads ? (simd ? 'video.mtsimd.wasm' : 'video.mt.wasm') : (simd ? 'video.simd.wasm' : 'video.decode.wasm')]
    .map(name => `vendor/${VERSION}/lib/av/${name}`);
}

function stylesheet(signal) {
  return new Promise((resolve, reject) => {
    const link = document.createElement('link');
    link.id = 'zoom-meetingsdk-css'; link.rel = 'stylesheet';
    link.href = BASE + `vendor/${VERSION}/ui/zoom-meetingsdk.css`;
    const fail = () => { link.remove(); reject(new Error('sdk_assets')); };
    signal.addEventListener('abort', fail, { once: true });
    link.onload = () => { signal.removeEventListener('abort', fail); resolve(); };
    link.onerror = () => { signal.removeEventListener('abort', fail); fail(); };
    document.head.append(link);
  });
}

export async function prepareSdk(signal) {
  const [module, manifest] = await Promise.all([
    import('@zoom/meetingsdk'),
    fetch(BASE + 'bootstrap.json', { signal, cache: 'no-cache', credentials: 'same-origin' }).then(async response => {
      if (!response.ok) throw new Error('sdk_assets');
      const value = await response.json();
      if (value.version !== VERSION || !Array.isArray(value.warmup) || value.warmup.length > 80) throw new Error('sdk_assets');
      return value;
    }),
  ]);
  if (signal.aborted) throw new Error('sdk_load_timeout');
  const { ZoomMtg } = module;
  ZoomMtg.setZoomJSLib(location.origin + BASE + `vendor/${VERSION}/lib`, '/av');
  ZoomMtg.setLogLevel('silent');
  // SDK preload completion is not media readiness. Never use it to signal "joined".
  const queue = [...manifest.warmup, ...wasmAssets()];
  await Promise.all([stylesheet(signal), ...Array.from({ length: 4 }, async () => {
    while (queue.length && !signal.aborted) await resource(queue.shift(), signal);
  })]);
  if (signal.aborted) throw new Error('sdk_load_timeout');
  Promise.resolve(ZoomMtg.preLoadWasm()).catch(() => {});
  await ZoomMtg.prepareWebSDK();
  await ZoomMtg.i18n.load('ru-RU');
  return ZoomMtg;
}
