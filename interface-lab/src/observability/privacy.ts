// Telemetry is an allowlist: never send arbitrary application strings or payloads.
const routeWords = new Set('api hr health state action candidate candidates employer recruiter auth session login logout me profile resume download upload availability booking bookings interview interviews meetings meeting notifications settings vacancies vacancy import intake sources search cancel confirm complete feedback transcript recording recordings media start stop status connection devices calendar slots schedule join access invite telegram miniapp hh outreach tasks runs archive draft account verify exchange consent original lab summary notes coach finish retry'.split(' '));
export function safeUrl(value: string, origin: string): string {
  try {
    const url = new URL(value, origin);
    if (url.origin !== origin) return url.protocol === 'https:' ? `${url.origin}/` : '[redacted]';
    let path = url.pathname;
    if (path.startsWith('/api/hr/')) path = '/' + path.split('/').slice(1, 12).map(part => routeWords.has(part) ? part : ':id').join('/');
    else if (!/^\/(?:iframe\.html|index\.html|employer|candidate|account\/candidate)?$/.test(path)
      && !/^\/(?:assets|site-assets)\/[a-zA-Z0-9_.-]+\.(?:js|css|woff2?)$/.test(path)) path = '/[redacted]';
    return origin + path;
  } catch { return '[redacted]'; }
}
export function safeStack(value: unknown, origin: string): string {
  if (typeof value !== 'string') return '';
  return value.split('\n').filter(line => /^\s*at\s/.test(line) || /@https?:\/\//.test(line)).slice(0, 24)
    .map(line => line.replace(/https?:\/\/[^\s)]+/g, raw => {
      const match = raw.match(/^(.*?)(:\d+:\d+)$/);
      return safeUrl(match?.[1] ?? raw, origin) + (match?.[2] ?? '');
    }).replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, '[redacted]')).join('\n').slice(0, 8000);
}
const attrKeys = new Set(['component','http.method','http.request.method','http.status_code','http.response.status_code','http.response_content_length','error','exception.type','rum.sessionId','session.id','app.area','app.version','deployment.environment']);
export function safeAttributes(input: Record<string, unknown>, origin: string): Record<string, string | number | boolean> {
  const result: Record<string, string | number | boolean> = {};
  for (const [key, value] of Object.entries(input)) {
    if (/^(?:http\.url|url\.full|location\.href|document\.url)$/.test(key) && typeof value === 'string') result[key] = safeUrl(value, origin);
    else if (key === 'exception.stacktrace') result[key] = safeStack(value, origin);
    else if (attrKeys.has(key) && ['string','number','boolean'].includes(typeof value)) result[key] = typeof value === 'string' ? value.slice(0, 160) : value as number | boolean;
    else if (/^(?:web_vital|webvitals|browser\.web_vital)\./.test(key) && typeof value === 'number') result[key] = value;
  }
  return result;
}
const structural = new Set(['_cssText','class','style','width','height','viewBox','d','fill','stroke','stroke-width','role','type','disabled','checked','selected','multiple','rel','xmlns']);
function safeCss(value: string): string {
  return value.replace(/url\([^)]*\)/gi, 'none').replace(/content\s*:[^;}]+/gi, 'content:""');
}
// rrweb calls eventProcessor before serializing both snapshots and mutations.
export function sanitizeReplay<T>(event: T, origin: string): T {
  const visit = (value: unknown, parent = ''): unknown => {
    if (Array.isArray(value)) return value.map(item => visit(item, parent));
    if (!value || typeof value !== 'object') return value;
    const source = value as Record<string, unknown>;
    const result: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(source)) {
      if (key === 'attributes' && item && !Array.isArray(item) && typeof item === 'object') {
        const attrs: Record<string, unknown> = {};
        for (const [attr, content] of Object.entries(item)) {
          if (content === null) { attrs[attr] = null; continue; }
          if (attr === 'href' && source.tagName === 'link' && String(content).split('?')[0].endsWith('.css')) attrs[attr] = safeUrl(String(content), origin);
          else if (structural.has(attr)) attrs[attr] = ['style','_cssText'].includes(attr) ? safeCss(String(content)) : String(content).slice(0, 2000);
        }
        result[key] = attrs;
      } else if (key === 'href' || key === 'url') result[key] = safeUrl(String(item), origin);
      else if (typeof item === 'string' && ['textContent','text','value'].includes(key)) {
        result[key] = source.isStyle ? safeCss(item) : '*'.repeat(Math.min(item.length, 2000));
      } else if (typeof item === 'string' && ['rule','cssText'].includes(key)) result[key] = safeCss(item);
      else if (key === 'payload') result[key] = {};
      else result[key] = visit(item, key);
    }
    return result;
  };
  return visit(event) as T;
}
