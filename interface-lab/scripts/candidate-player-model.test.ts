import assert from 'node:assert/strict';
import test from 'node:test';
import { interviewParagraphs, interviewTime, interviewTimeline } from '../src/scenes/interview-review/candidate-player-model.ts';
import { parseMeetingTranscript } from '../src/scenes/connected-journey/transcript.ts';

test('DSA speech keeps every source word and speaker while making readable paragraphs', () => {
  const cues = parseMeetingTranscript('[00:02-00:05] Р: Первый вопрос.\n[00:05-00:09] К: Точный ответ.\n[00:09-00:13] К: Его продолжение.\n[00:14-00:16] Р: Уточнение.');
  const result = interviewParagraphs(cues);
  assert.deepEqual(result.map(row => [row.start, row.end, row.speaker]), [[2,5,'Рекрутер'],[5,13,'Кандидат'],[14,16,'Рекрутер']]);
  assert.equal(result.map(row => row.text).join(' '), cues.map(row => row.fragment).join(' '));
  assert.equal(cues.length, 4);
});

test('a pause or a long answer starts another paragraph rather than creating one giant block', () => {
  const cues = parseMeetingTranscript('[00:01-00:08] К: Первая фраза.\n[00:12-00:20] К: После паузы.\n[00:20-00:32] К: Длинное продолжение.');
  assert.deepEqual(interviewParagraphs(cues).map(row => row.start), [1,12,20]);
});

test('plain transcript has no invented timing and VTT speakers remain separate', () => {
  assert.deepEqual(interviewParagraphs(parseMeetingTranscript('Речь без таймкодов.')), []);
  const cues = parseMeetingTranscript('WEBVTT\n\n00:01.000 --> 00:03.500\n<v A>Один.</v>\n\n00:03.500 --> 00:04.000\n<v B>Два.</v>');
  assert.deepEqual(interviewParagraphs(cues).map(row => row.text), ['Один.', 'Два.']);
});

test('progress segments cover the whole recording once; malformed or duplicate boundaries cannot create negative segments', () => {
  const chapter = (seconds: number | null) => ({ seconds, title:'Глава', detail:'', fragment:'', source:'' });
  assert.deepEqual(interviewTimeline([chapter(20),chapter(null),chapter(5),chapter(5),chapter(-1),chapter(30),chapter(NaN)],30), [
    {start:0,end:5},{start:5,end:20},{start:20,end:30},
  ]);
  assert.deepEqual(interviewTimeline([], 0), []);
  assert.deepEqual(interviewTimeline([], Infinity), []);
});

test('time labels retain hours and do not expose invalid media metadata', () => {
  assert.equal(interviewTime(443.9), '07:23');
  assert.equal(interviewTime(3601), '1:00:01');
  assert.equal(interviewTime(NaN), '00:00');
});
