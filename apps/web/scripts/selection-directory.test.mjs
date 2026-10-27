// Node-only pagination tests. No browser, live API, model, or delivery calls.
import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const bundle = await build({ entryPoints: [fileURLToPath(new URL('../src/scenes/selection-workbench/directory.ts', import.meta.url))], bundle: true, platform: 'node', format: 'esm', write: false });
const { loadCandidateDirectory, recommendationCandidateIds } = await import('data:text/javascript;base64,' + Buffer.from(bundle.outputFiles[0].text).toString('base64'));
const ready = { state: 'ready', allPartsReady: true, recordingCount: 1, transcriptCount: 1 };
const people = count => Array.from({ length: count }, (_, i) => ({ id: 'candidate-' + i, name: 'Candidate ' + i, hrInterview: ready }));

function stub(t, candidates, change = value => value) {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (input, init) => {
    const url = new URL(input, 'https://local.invalid'), offset = Number(url.searchParams.get('offset')), limit = Number(url.searchParams.get('limit'));
    calls.push({ url, offset, limit });
    assert.equal(url.pathname, '/api/hr/selection/candidates');
    assert.equal(init.method, 'GET'); assert.equal(init.body, undefined);
    assert.equal(init.headers.Authorization, 'Bearer fixture-token');
    assert.ok(limit > 0 && limit <= 500);
    const part = candidates.slice(offset, offset + limit);
    const page = { candidates: part, count: candidates.length, offset, limit, nextOffset: offset + part.length < candidates.length ? offset + part.length : null };
    const result = await change(page, calls.length, calls);
    return result instanceof Response ? result : Response.json(result);
  });
  return calls;
}

test('loads every page in server order, including the candidate beyond 200 and 500', async t => {
  const source = people(620), calls = stub(t, source);
  const result = await loadCandidateDirectory('fixture-token');
  assert.deepEqual(result, { candidates: source, count: 620 });
  assert.equal(result.candidates.find(p => p.id === 'candidate-550').name, 'Candidate 550');
  assert.deepEqual(calls.map(c => c.offset), [0, 500]);
  assert.deepEqual(calls.map(c => c.url.searchParams.has('vacancyId')), [false, false]);
  assert.equal(recommendationCandidateIds(result.candidates).includes('candidate-550'), true);
});

test('preserves the exact vacancy scope and page limit on all three pages', async t => {
  const source = people(1001), calls = stub(t, source);
  const vacancyId = 'vacancy /&?= + Ю';
  const result = await loadCandidateDirectory('fixture-token', vacancyId);
  assert.equal(result.candidates.length, 1001);
  assert.deepEqual(calls.map(c => c.offset), [0, 500, 1000]);
  assert.deepEqual(calls.map(c => c.url.searchParams.get('vacancyId')), [vacancyId, vacancyId, vacancyId]);
  assert.equal(new Set(calls.map(c => c.limit)).size, 1);
});

test('an intermediate HTTP failure rejects the whole read without returning partial candidates', async t => {
  let result;
  const calls = stub(t, people(1100), (page, n) => n === 2 ? Response.json({ error: 'Temporarily unavailable' }, { status: 503 }) : page);
  await assert.rejects(async () => { result = await loadCandidateDirectory('fixture-token'); }, /весь каталог/);
  assert.equal(result, undefined);
  assert.deepEqual(calls.map(c => c.offset), [0, 500]);
});

test('an explicit retry starts at zero and resolves only the new complete traversal', async t => {
  const source = people(501), calls = stub(t, source, (page, n) => n === 2 ? Response.json({ error: 'Lost page' }, { status: 500 }) : page);
  await assert.rejects(loadCandidateDirectory('fixture-token'), /весь каталог/);
  assert.deepEqual(await loadCandidateDirectory('fixture-token'), { candidates: source, count: source.length });
  assert.deepEqual(calls.map(c => c.offset), [0, 500, 0, 500]);
});

test('authentication failure on a later page keeps the expiry signal', async t => {
  stub(t, people(501), (page, n) => n === 2 ? Response.json({ error: 'Session expired' }, { status: 401 }) : page);
  await assert.rejects(loadCandidateDirectory('fixture-token'), e => e.status === 401 && e.message === 'Session expired');
});

test('rejects missing, invalid, non-progressing, premature or skipping continuation', async t => {
  for (const nextOffset of [undefined, null, 0, -1, 499, 501, 500.5, '500', true]) {
    await t.test(String(nextOffset), async child => {
      const calls = stub(child, people(501), page => ({ ...page, nextOffset }));
      await assert.rejects(loadCandidateDirectory('fixture-token'), /весь каталог/);
      assert.equal(calls.length, 1);
    });
  }
});

test('rejects duplicate identities within or between pages instead of silently deduplicating', async t => {
  for (const index of [1, 500]) {
    await t.test('duplicate at ' + index, async child => {
      const source = people(501); source[index] = source[0]; stub(child, source);
      await assert.rejects(loadCandidateDirectory('fixture-token'), /весь каталог/);
    });
  }
});

test('rejects incomplete pages and inconsistent metadata rather than treating them as the end', async t => {
  const mutations = {
    missingCandidates: page => ({ ...page, candidates: undefined }),
    missingCount: page => ({ ...page, count: undefined }),
    stringCount: page => ({ ...page, count: '501' }),
    negativeCount: page => ({ ...page, count: -1 }),
    shortPage: page => ({ ...page, candidates: page.candidates.slice(1) }),
    emptyIntermediatePage: page => ({ ...page, candidates: [] }),
    wrongOffset: page => ({ ...page, offset: 1 }),
    wrongLimit: page => ({ ...page, limit: 200 }),
    missingIdentity: page => ({ ...page, candidates: [{}, ...page.candidates.slice(1)] }),
  };
  for (const [name, mutate] of Object.entries(mutations)) {
    await t.test(name, async child => {
      stub(child, people(501), mutate);
      await assert.rejects(loadCandidateDirectory('fixture-token'), /весь каталог/);
    });
  }
});

test('rejects total drift, a missing final item, and an unexpected final continuation', async t => {
  const mutations = {
    changedTotal: page => ({ ...page, count: 502 }),
    missingFinalItem: page => ({ ...page, candidates: [] }),
    continuedPastEnd: page => ({ ...page, nextOffset: 501 }),
  };
  for (const [name, mutate] of Object.entries(mutations)) {
    await t.test(name, async child => {
      stub(child, people(501), (page, n) => n === 2 ? mutate(page) : page);
      await assert.rejects(loadCandidateDirectory('fixture-token'), /весь каталог/);
    });
  }
});

test('accepts an explicitly empty catalog with null continuation', async t => {
  const calls = stub(t, []);
  assert.deepEqual(await loadCandidateDirectory('fixture-token'), { candidates: [], count: 0 });
  assert.equal(calls.length, 1);
});

test('requires recording AND transcript AND all parts before including any candidate in matching', () => {
  const source = people(7);
  source[1].hrInterview = { ...ready, recordingCount: 0 };
  source[2].hrInterview = { ...ready, transcriptCount: 0 };
  source[3].hrInterview = { ...ready, allPartsReady: false };
  source[4].hrInterview = { ...ready, state: 'processing' };
  source[5].hrInterview = undefined; source[5].interviewStatus = 'completed';
  source[6].hrInterview = { ...ready, recordingCount: null };
  assert.deepEqual(recommendationCandidateIds(source), ['candidate-0']);
});

test('accepts exactly 1000 ready candidates and explicitly rejects 1001 without slicing', () => {
  const source = people(1001);
  assert.throws(() => recommendationCandidateIds(source), /1001.*Сузьте пул до 1000/);
  source[1000].hrInterview = { ...ready, transcriptCount: 0 };
  const ids = recommendationCandidateIds(source);
  assert.equal(ids.length, 1000);
  assert.deepEqual(ids, source.slice(0, 1000).map(p => p.id).sort());
  assert.equal(source.length, 1001);
});
