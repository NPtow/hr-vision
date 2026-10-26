// Synthetic local API for Storybook. No forwarding, provider calls or persistence.
import { createServer } from 'node:http';
import { makeFixture } from './selection-vacancy.fixtures.mjs';
const state = makeFixture();
const port = Number(process.env.SELECTION_PREVIEW_PORT ?? 8498);
createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (!url.pathname.startsWith('/api/hr/')) { res.writeHead(404); return res.end(); }
  const chunks = []; for await (const chunk of req) chunks.push(chunk);
  try {
    const value = state.request(req.method, url.pathname, chunks.length ? JSON.parse(Buffer.concat(chunks)) : null);
    res.writeHead(value?.httpStatus ?? 200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(value));
  } catch { res.writeHead(400, { 'content-type': 'application/json' }); res.end(JSON.stringify({ error: 'Проверьте тестовый запрос' })); }
}).listen(port, '127.0.0.1', () => process.stdout.write(`Synthetic selection API on 127.0.0.1:${port}\n`));
