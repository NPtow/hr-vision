import test from 'node:test';
import assert from 'node:assert/strict';
import { candidateCountLabel, deriveRequirements, getDemoMatches } from '../src/matching-demo.ts';

test('candidate counts use the right Russian noun form', () => {
  assert.equal(candidateCountLabel(0), '0 кандидатов');
  assert.equal(candidateCountLabel(1), '1 кандидат');
  assert.equal(candidateCountLabel(2), '2 кандидата');
  assert.equal(candidateCountLabel(3), '3 кандидата');
  assert.equal(candidateCountLabel(11), '11 кандидатов');
  assert.equal(candidateCountLabel(21), '21 кандидат');
});

test('a client-return task connects to matching interview evidence', () => {
  const result = getDemoMatches('Вернуть ушедших клиентов и развивать повторные B2B-продажи.');
  assert.equal(result.recognized, true);
  assert.deepEqual(result.requirements.map(item => item.id), ['returning-clients', 'b2b-sales']);
  assert.deepEqual(result.matches.slice(0, 2).map(item => item.id), ['anna-mironova', 'elena-orlova']);
  assert.match(result.matches[0].evidence[0].quote, /почему клиент перестал заказывать/);
  assert.match(result.matches[0].interest, /клиентскую базу/);
});

test('a CRM task changes both the shortlist and the reasons', () => {
  const result = getDemoMatches('Настроить Битрикс24 и CRM-воронку, чтобы сделки не терялись.');
  assert.deepEqual(result.requirements.map(item => item.id), ['bitrix24', 'crm-workflows']);
  assert.deepEqual(result.matches.slice(0, 2).map(item => item.id), ['mikhail-belov', 'alina-kim']);
  assert.ok(!result.matches.some(item => item.id === 'anna-mironova'));
  for (const match of result.matches.slice(0, 2)) {
    assert.equal(match.evidence.length, 2);
    assert.ok(match.evidence.some(item => item.criterionId === 'bitrix24'));
    assert.ok(match.evidence.every(item => item.quote.length > 30 && /^\d\d:\d\d$/.test(item.timecode)));
  }
});

test('team/process and project work produce distinct relevant leaders', () => {
  const team = getDemoMatches('Выстроить процессы в отделе и развивать команду продаж.');
  assert.equal(team.matches[0].id, 'ilya-sokolov');
  assert.ok(team.matches.every(match => match.evidence.some(item => ['team-leadership', 'process-ownership'].includes(item.criterionId))));
  const project = getDemoMatches('Вести проекты в срок и координировать работу команды.');
  assert.equal(project.matches[0].id, 'maria-volkova');
  assert.match(project.matches[0].evidence.find(item => item.criterionId === 'project-delivery')!.quote, /этапы/);
});

test('unknown or empty input never presents a fabricated search result', () => {
  for (const text of ['', '   ', 'Нужен кондитер в пекарню', 'Поддерживать станки на заводе']) {
    assert.deepEqual(deriveRequirements(text), []);
    assert.deepEqual(getDemoMatches(text), { recognized: false, requirements: [], matches: [] });
  }
});

test('manual criteria are authoritative and changing a chip changes candidates', () => {
  const originalText = 'Вернуть ушедших клиентов';
  const selectedCrm = getDemoMatches(originalText, ['bitrix24']);
  assert.deepEqual(selectedCrm.requirements.map(item => item.id), ['bitrix24']);
  assert.deepEqual(selectedCrm.matches.map(item => item.id), ['mikhail-belov', 'alina-kim']);
  const cleared = getDemoMatches(originalText, []);
  assert.equal(cleared.matches.length, 0);
  assert.equal(cleared.recognized, false);
  assert.equal(getDemoMatches('Новая задача', ['project-delivery']).matches[0].id, 'maria-volkova');
  assert.equal(getDemoMatches(originalText, ['nonexistent']).matches.length, 0);
});

test('mixed requirements expose missing evidence instead of overstating fit', () => {
  const result = getDemoMatches('Вернуть клиентов и работать в Битрикс24');
  const anna = result.matches.find(match => match.id === 'anna-mironova')!;
  assert.ok(anna);
  assert.deepEqual(anna.missing.map(item => item.id), ['bitrix24']);
  assert.ok(anna.evidence.every(item => item.criterionId !== 'bitrix24'));
  assert.match(anna.followUp, /пока не использовала/);
});
