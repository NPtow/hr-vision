import assert from 'node:assert/strict';
import test from 'node:test';
import { evidenceRecording, orderedRecordings, selectedTranscript } from '../src/scenes/interview-review/recording-transcript.ts';

const materials = () => ({ recordings: [{ id: 'part-b', url: '/b', partIndex: 1, duration: 50 }, { id: 'part-a', url: '/a', partIndex: 0, duration: 5 }],
  transcripts: [{ id: 'text-a', recordingId: 'part-a', text: 'Первая часть' }, { id: 'text-b', recordingId: 'part-b', text: 'Вторая часть' }],
  transcription: { status: 'ready' } });
const evidence = { id: 'fragment', text: 'Источник', time: '00:00', seconds: 0, recordingId: 'part-b', transcriptId: 'text-b' };

test('stable recording order uses complete unique ordinals, duplicate batch ordinals fall back to IDs', () => {
  const data = materials();
  assert.deepEqual(orderedRecordings(data.recordings).map(r => r.id), ['part-a', 'part-b']);
  data.recordings.reverse();
  assert.deepEqual(orderedRecordings(data.recordings).map(r => r.id), ['part-a', 'part-b']);
  data.recordings.forEach(r => { r.partIndex = 8; });
  assert.deepEqual(orderedRecordings(data.recordings).map(r => r.id), ['part-a', 'part-b']);
});

test('selected text is resolved per recording, never joined or assigned by array index', () => {
  const data = materials();
  assert.equal(selectedTranscript(data, 'part-b')?.text, 'Вторая часть');
  assert.equal(selectedTranscript(data, 'part-a')?.text, 'Первая часть');
  assert.equal(selectedTranscript(data, 'foreign'), undefined);
  data.transcripts.push({ id: 'duplicate', recordingId: 'part-b', text: 'Неоднозначный' });
  assert.equal(selectedTranscript(data, 'part-b'), undefined);
});

test('pending/error removes earlier selected text even when stale payload still contains it', () => {
  for (const status of ['processing', 'error', 'not_recorded']) {
    const data = materials(); data.transcription.status = status;
    assert.equal(selectedTranscript(data, 'part-a'), undefined);
    assert.equal(evidenceRecording(data, evidence), undefined);
  }
});

test('evidence selects exact other recording before local zero-time seek and rejects stale transcript', () => {
  assert.equal(evidenceRecording(materials(), evidence), 'part-b');
  assert.equal(evidenceRecording(materials(), { ...evidence, transcriptId: 'old-version' }), undefined);
  assert.equal(evidenceRecording(materials(), { ...evidence, recordingId: 'part-a' }), undefined);
  assert.equal(evidenceRecording(materials(), { ...evidence, seconds: 60 }), undefined);
  assert.equal(evidenceRecording(materials(), { ...evidence, seconds: NaN }), undefined);
});

test('legacy association is unique single only; multipart missing link stays unavailable', () => {
  const single = { recordings: [{ id: 'one', url: '/one' }], transcripts: [{ id: 'old', text: 'Текст' }], transcription: { status: 'ready' } };
  assert.equal(selectedTranscript(single, 'one')?.id, 'old');
  single.recordings.push({ id: 'two', url: '/two' });
  assert.equal(selectedTranscript(single, 'one'), undefined);
  assert.equal(evidenceRecording(materials(), { ...evidence, recordingId: undefined }), undefined);
});
