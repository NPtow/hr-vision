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
    else if (key === 'exception.stacktrace' || key === 'error.stack') result[key] = safeStack(value, origin);
    else if (key === 'error.message') result[key] = typeof value === 'string' && /^(?:Type|Reference|Syntax|Range|URI|Eval)?Error$/.test(value) ? value : 'Error (message redacted)';
    else if (attrKeys.has(key) && ['string','number','boolean'].includes(typeof value)) result[key] = typeof value === 'string' ? value.slice(0, 160) : value as number | boolean;
    else if (/^(?:web_vital|webvitals|browser\.web_vital)\./.test(key) && typeof value === 'number') result[key] = value;
  }
  return result;
}
const structural = new Set(['class','width','height','viewBox','viewbox','d','fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','cx','cy','r','x','y','x1','x2','y1','y2','points','transform','role','type','disabled','checked','selected','multiple','rel','xmlns','open','hidden','dir','lang','colspan','rowspan','tabindex','aria-expanded','aria-selected','aria-checked','aria-hidden','data-state','data-side','data-align','data-orientation','data-disabled','data-placeholder']);
const mask = (text: string) => text.replace(/\S/g, '*');
export function createTextMask(copy: readonly string[]) {
  const readable = new Set(copy.map(text => text.replace(/\s+/g, ' ').trim()));
  return (text: string): string => !/[\p{L}\p{N}]/u.test(text) || readable.has(text.replace(/\s+/g, ' ').trim()) ? text : mask(text);
}
export function safeAssetUrl(value: string, origin: string): string | undefined {
  try {
    const url = new URL(value, origin);
    if (url.origin === origin && /^\/(?:assets|site-assets|brand)\/[a-zA-Z0-9_./-]+\.(?:css|woff2?|ttf|otf|svg|png|webp|ico)$/.test(url.pathname)) return origin + url.pathname;
  } catch { /* untrusted URLs are omitted */ }
}
export function safeCss(value: string, origin: string): string {
  return value.replace(/url\(\s*(['"]?)(.*?)\1\s*\)/gi, (_match, _quote, url) => {
    const safe = safeAssetUrl(url, origin);
    return safe ? `url("${safe}")` : 'none';
  }).replace(/(^|[;{])(\s*)content\s*:[^;}]+/gi, '$1$2content:""');
}
type ReplayNode = {tag?: string; parent?: number; style: boolean};
// rrweb 2.1 does not set isStyle on text nodes. Remember their parent, including
// across incremental mutations; otherwise a stylesheet is mistaken for private text.
export function createReplaySanitizer(origin: string, readableText = createTextMask([])) {
  const nodes = new Map<number, ReplayNode>();
  const register = (value: unknown, parent?: number): void => {
    if (!value || typeof value !== 'object') return;
    const node = value as Record<string, unknown>;
    if (typeof node.id !== 'number') return;
    const tag = typeof node.tagName === 'string' ? node.tagName : undefined;
    const style = tag === 'style' || (tag === undefined && Boolean(parent && nodes.get(parent)?.style));
    nodes.set(node.id, {tag, parent, style});
    if (Array.isArray(node.childNodes)) node.childNodes.forEach(child => register(child, node.id as number));
  };
  const visit = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(item => visit(item));
    if (!value || typeof value !== 'object') return value;
    const source = value as Record<string, unknown>;
    const result: Record<string, unknown> = {};
    const info = typeof source.id === 'number' ? nodes.get(source.id) : undefined;
    const tag = source.tagName ?? info?.tag;
    for (const [key, item] of Object.entries(source)) {
      if (key === 'attributes' && item && !Array.isArray(item) && typeof item === 'object') {
        const attrs: Record<string, unknown> = {};
        for (const [attr, content] of Object.entries(item)) {
          if (content === null) { attrs[attr] = null; continue; }
          if ((attr === 'href' && tag === 'link') || (attr === 'src' && tag === 'img')) {
            const asset = safeAssetUrl(String(content), origin);
            if (asset) attrs[attr] = asset;
          }
          else if (attr === 'id' && ['root','storybook-root','storybook-docs'].includes(String(content))) attrs[attr] = content;
          else if (/^rr_(width|height|scrollLeft|scrollTop)$/.test(attr) && /^[0-9.]+(?:px)?$/.test(String(content))) attrs[attr] = content;
          else if (attr === '_cssText' && ['style','link'].includes(String(tag))) attrs[attr] = safeCss(String(content), origin);
          else if (attr === 'style') {
            attrs[attr] = typeof content === 'string' ? safeCss(content, origin) : Object.fromEntries(Object.entries(content as Record<string, unknown>).map(([property, value]) => [property,
              property === 'content' ? false : Array.isArray(value) ? [safeCss(String(value[0]), origin), value[1]] : value === false ? false : safeCss(String(value), origin)]));
          }
          else if (['value','placeholder','title','alt','aria-label'].includes(attr)) attrs[attr] = attr === 'value' ? mask(String(content)) : readableText(String(content));
          else if (structural.has(attr)) attrs[attr] = String(content).slice(0, 2000);
        }
        result[key] = attrs;
      } else if (key === 'href' || key === 'url') result[key] = safeUrl(String(item), origin);
      else if (typeof item === 'string' && ['textContent','text','value'].includes(key)) {
        result[key] = source.isStyle || info?.style ? safeCss(item, origin) : key === 'text' ? mask(item) : readableText(item);
      } else if (typeof item === 'string' && ['rule','cssText'].includes(key)) result[key] = safeCss(item, origin);
      else if (key === 'payload') result[key] = {};
      else result[key] = visit(item);
    }
    return result;
  };
  return <T>(event: T): T => {
    const e = event as {type?: number; data?: {node?: unknown; adds?: {node: unknown; parentId: number}[]; removes?: {id: number}[]}};
    if (e.type === 2) { nodes.clear(); register(e.data?.node); }
    for (const addition of e.data?.adds ?? []) register(addition.node, addition.parentId);
    const result = visit(event) as T;
    if (e.data?.removes?.length) {
      const removed = new Set(e.data.removes.map(node => node.id));
      for (const [id, info] of nodes) if (removed.has(id) || (info.parent !== undefined && removed.has(info.parent))) { removed.add(id); nodes.delete(id); }
    }
    return result;
  };
}
// Stateless entry point for callers sanitizing a single self-contained event.
export function sanitizeReplay<T>(event: T, origin: string): T { return createReplaySanitizer(origin)(event); }
