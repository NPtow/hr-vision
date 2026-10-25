import assert from 'node:assert/strict';
import test from 'node:test';
import { interviewParagraphs, interviewTime, interviewTimeline, interviewTranscriptSections, interviewSearchGroups } from '../src/scenes/interview-review/candidate-player-model.ts';
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


const chapter = (title: string, seconds: number | null) => ({ title, seconds, detail: '', fragment: '', source: '' });
test('chapter boundaries stop visual paragraph merging without changing words, speakers or original cue times', () => {
  const cues = parseMeetingTranscript('[00:00-00:05] К: Первый факт.\n[00:05-00:09] К: Второй факт.\n[00:09-00:12] Р: Вопрос.');
  const result = interviewTranscriptSections(cues, [chapter('Начало', 0), chapter('Другой опыт', 5)]);
  assert.deepEqual(result.paragraphs.map(row => [row.start, row.end, row.text]), [[0,5,'Первый факт.'],[5,9,'Второй факт.'],[9,12,'Вопрос.']]);
  assert.deepEqual(result.sections.map(section => section.rows.map(({index}) => index)), [[0],[1,2]]);
  assert.equal(result.sections.flatMap(section => section.rows).map(({row}) => row.text).join(' '), cues.map(cue => cue.fragment).join(' '));
});

test('cues crossing a boundary and the prelude remain whole and appear exactly once', () => {
  const cues = parseMeetingTranscript('[00:01-00:04] Р: До главы.\n[00:04-00:08] К: Через границу.\n[00:09-00:12] Р: После границы.');
  const result = interviewTranscriptSections(cues, [chapter('Глава',6)]);
  assert.deepEqual(result.sections.map(section => section.rows.map(({row}) => [row.start,row.end])), [[[1,4],[4,8]],[[9,12]]]);
  assert.equal(result.sections[0].chapters.length,0);
  assert.equal(cues.length,3);
});

test('source-derived transcript cues never become chapter headings, including voice-less VTT', () => {
  const cues = parseMeetingTranscript('WEBVTT\n\n00:01.000 --> 00:03.000\nРеплика без спикера.\n\n00:04.000 --> 00:05.000\n<v A>Реплика со спикером.</v>');
  const result = interviewTranscriptSections(cues, cues);
  assert.equal(result.sections.length,1);
  assert.equal(result.sections[0].chapters.length,0);
  assert.equal(result.paragraphs.length,2);
  assert.deepEqual(interviewTimeline(cues, 10), [{ start: 0, end: 10 }]);
});

test('search navigation counts whole passages instead of each highlighted paragraph', () => {
  const paragraphs = interviewParagraphs(parseMeetingTranscript('[00:01-00:05] Р: Вопрос.\n[00:05-00:10] К: Ответ.\n[00:10-00:15] Р: Уточнение.\n[02:00-02:04] К: Другой ответ.'));
  assert.deepEqual(interviewSearchGroups(paragraphs, [{startMs:1000,endMs:15000},{startMs:120000,endMs:124000}]), [[0,1,2],[3]]);
  assert.deepEqual(interviewSearchGroups(paragraphs, [{startMs:15000,endMs:20000}]), []);
});

test('unsorted and repeated boundaries share one heading location; untimed descriptions never acquire a video clock', () => {
  const invalid = [chapter('Без времени',null),chapter('Ошибочное время',NaN),chapter('Отрицательное время',-1)];
  const input = [chapter('Поздняя',10),chapter('Ранняя',0),chapter('Ранняя',0),chapter('Ещё тема',0), ...invalid];
  const result = interviewTranscriptSections(parseMeetingTranscript('[00:00-00:05] К: Факт.\n[00:11-00:14] Р: Вопрос.'), input);
  assert.deepEqual(result.sections.slice(0,2).map(section => [section.start,section.chapters.map(ch => ch.title)]), [[0,['Ранняя','Ещё тема']],[10,['Поздняя']]]);
  assert.deepEqual(result.sections.slice(2).map(section => section.start),[null,null,null]);
  assert.deepEqual(result.sections.flatMap(section => section.rows).map(({index}) => index),[0,1]);
  assert.equal(input[0].title,'Поздняя');
});

test('prepared quote chapters remain headings and a recording without chapters remains a plain transcript', () => {
  const cues = parseMeetingTranscript('[00:00-00:03] К: Точная речь.');
  const result = interviewTranscriptSections(cues,[{...chapter('Опыт',0),isQuote:true,fragment:'Точная речь.'}]);
  assert.equal(result.sections[0].chapters[0].title,'Опыт');
  assert.equal(interviewTranscriptSections(cues,[]).sections[0].chapters.length,0);
  assert.deepEqual(interviewTranscriptSections([],[]).paragraphs,[]);
});
