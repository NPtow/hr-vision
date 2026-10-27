import { strict as assert } from 'node:assert';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { adaptEngineCandidateProfile } from './assessment-adapter.ts';
import type { EngineCandidateProfile } from './assessment-adapter.ts';
import { demoAdapterContext, demoAssessment, demoTurns, resolveDemoSeconds } from './assessment-fixture.ts';
import { canonicalSources, engineGolden, resolvedFixtureExcerpts } from './engine-v1-sales-b2b-partial.ts';

type Mutable<T> = { -readonly [K in keyof T]: T[K] extends readonly (infer Item)[] ? Mutable<Item>[] : T[K] extends object ? Mutable<T[K]> : T[K] };
const mutableProfile = (): Mutable<EngineCandidateProfile> => JSON.parse(JSON.stringify(engineGolden));
const hash = (text: string | Uint8Array) => createHash('sha256').update(text).digest('hex');

test('synthetic canonical sources and every quote match original byte ranges and SHA256', () => {
  assert.equal(engineGolden.synthetic, true);
  for (const source of canonicalSources) assert.equal(hash(source.text), source.sha256);
  for (const excerpt of resolvedFixtureExcerpts) {
    const source = canonicalSources.find(item => item.sourceId === excerpt.sourceId)!;
    const raw = Buffer.from(source.text).subarray(excerpt.byteFrom, excerpt.byteTo);
    assert.equal(raw.toString('utf8'), excerpt.text);
    assert.equal(hash(raw), excerpt.quoteSha256);
    assert.equal(excerpt.sourceSha256, source.sha256);
  }
});

test('actual readings preserve criterion levels and unknown; no target or overall score is derived', () => {
  assert.deepEqual(demoAssessment.criteria.map(item => [item.id, item.state, item.level, item.target]), [
    ['A10', 'supported', 4, null], ['A07', 'supported', 4, null], ['A04', 'supported', 2, null],
    ['A06.1', 'unknown', null, null], ['A11.1', 'unknown', null, null],
  ]);
  const contaminated = { ...mutableProfile(), overall_score: 97, rank: 1 };
  const result = adaptEngineCandidateProfile(contaminated, demoAdapterContext);
  assert.equal('overall_score' in result, false);
  assert.equal('score' in result, false);
  assert.equal('rank' in result, false);
});

test('unknown and conflict cannot become zero or a displayed level even if upstream sends one', () => {
  const profile = mutableProfile();
  const raw = profile.body.families[0].criteria;
  const unknown = raw.find(item => item.criterion_id === 'A11.1')!;
  unknown.level = 0;
  const conflict = raw.find(item => item.criterion_id === 'A10')!;
  conflict.state = 'conflict';
  conflict.level = 4;
  const result = adaptEngineCandidateProfile(profile, demoAdapterContext);
  assert.equal(result.criteria.find(item => item.id === 'A11.1')!.level, null);
  assert.equal(result.criteria.find(item => item.id === 'A10')!.level, null);
  assert.equal(result.criteria.find(item => item.id === 'A10')!.state, 'conflict');
});

test('only the supplied source-scoped host mapping yields seconds; no guessed fallback', () => {
  assert.deepEqual(demoAssessment.criteria.flatMap(item => item.evidence.map(ev => ev.seconds)), [2, 8, 15]);
  const noTimeline = adaptEngineCandidateProfile(engineGolden, { ...demoAdapterContext, resolveSeconds: () => null });
  assert.equal(noTimeline.criteria.flatMap(item => item.evidence).length, 3);
  assert.ok(noTimeline.criteria.flatMap(item => item.evidence).every(item => item.seconds === null && item.time === '' && item.availability === 'text_only'));
  assert.equal(demoTurns.length, 7);
  assert.deepEqual(demoTurns.map(item => item.seconds), [null, 2, null, 8, null, null, 15]);
});

test('an evidence locator cannot borrow the time of a different source or turn', () => {
  const excerpt = resolvedFixtureExcerpts[0];
  const locator = { ...excerpt, turnIds: [4] };
  assert.equal(resolveDemoSeconds(locator), 8);
  assert.equal(resolveDemoSeconds({ ...locator, sourceSha256: 'a'.repeat(64) }), null);
  assert.equal(resolveDemoSeconds({ ...locator, sourceId: 'another-recording' }), null);
  assert.equal(resolveDemoSeconds({ ...locator, turnIds: [2] }), null);
});

test('missing sources never block the profile or fabricate evidence', () => {
  const result = adaptEngineCandidateProfile(engineGolden, { ...demoAdapterContext, sources: [] });
  assert.equal(result.criteria.length, 5);
  assert.equal(result.criteria[0].state, 'supported');
  assert.equal(result.criteria[0].level, 4);
  assert.equal(result.criteria[0].evidence.length, 0);
  assert.match(result.criteria[0].limitation, /недоступен/);
});

test('changed quote hash, range or source bytes withhold only that excerpt', () => {
  const profile = mutableProfile();
  const evidence = profile.body.evidence;
  evidence.find(item => item.evidence_id === 'ev_d6615cd7195607c9f6e9')!.quote_sha256 = '0'.repeat(64);
  const changedHash = adaptEngineCandidateProfile(profile, demoAdapterContext);
  assert.equal(changedHash.criteria.find(item => item.id === 'A10')!.evidence.length, 0);
  assert.equal(changedHash.criteria.find(item => item.id === 'A07')!.evidence.length, 1);
  const changedBytes = adaptEngineCandidateProfile(engineGolden, {
    ...demoAdapterContext,
    sources: canonicalSources.map(item => ({ ...item, text: item.text.replace('Можно', 'Нельзя') })),
  });
  assert.equal(changedBytes.criteria.find(item => item.id === 'A10')!.evidence.length, 0);
  assert.equal(changedBytes.criteria.length, 5);
});

test('an unsupported kernel contract is not silently interpreted', () => {
  assert.throws(() => adaptEngineCandidateProfile({ ...mutableProfile(), engine_contract: 'unknown-v4' }, demoAdapterContext), /Unsupported/);
});
