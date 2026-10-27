import assert from 'node:assert/strict';
import { parseMeetingTranscript } from '../src/scenes/connected-journey/transcript.ts';

const daily = parseMeetingTranscript('WEBVTT\n\ntranscript:1\n00:00:06.178 --> 00:00:09.578\n<v>Анна:</v>Работала с клиентами.\n');
assert.equal(daily[0].title, 'Анна');
assert.equal(daily[0].fragment, 'Работала с клиентами.');
assert.equal(daily[0].seconds, 6.178);
const standard = parseMeetingTranscript('WEBVTT\n\n00:02.000 --> 00:04.000\n<v Иван>Вопрос &amp; пример</v>\n');
assert.equal(standard[0].title, 'Иван');
assert.equal(standard[0].fragment, 'Вопрос & пример');
assert.equal(standard[0].seconds, 2);
assert.equal(parseMeetingTranscript('WEBVTT\n').length, 0);
assert.equal(parseMeetingTranscript('Обычный текст без таймкодов.').length, 0);
assert.equal(parseMeetingTranscript('00:70.000 --> 00:71.000\nОшибочное время').length, 0);
assert.equal(parseMeetingTranscript('00:04.000 --> 00:02.000\nОбратный диапазон').length, 0);
console.log('10 transcript checks passed: real Daily voice markup, standard VTT, absent/invalid timestamps.');
