"""Recording-local transcript evidence: no network or model calls."""
import copy
import unittest

from domain import Problem
import transcript_bundle as tb


def materials():
    return {'recordings': [{'id': 'part-a', 'partIndex': 0}, {'id': 'part-b', 'partIndex': 1}],
        'transcription': {'status': 'ready'}, 'transcripts': [
            {'id': 'text-' + p, 'recordingId': 'part-' + p, 'selectionRevision': 2,
             'text': 'Действие ' + p, 'segments': [
                {'start_ms': 0, 'end_ms': 2000, 'speaker': 'speaker_0', 'text': 'Действие ' + p}]} for p in ('a', 'b')]}


class BundleTest(unittest.TestCase):
    def test_parts_keep_local_zero_times_unique_voices_and_chunk_ids(self):
        value = tb.bundle(materials())
        rows = tb.chunks(value)
        self.assertEqual([r['startMs'] for r in rows], [0, 0])
        self.assertEqual([r['recordingId'] for r in rows], ['part-a', 'part-b'])
        self.assertEqual([r['transcriptId'] for r in rows], ['text-a', 'text-b'])
        self.assertNotEqual(rows[0]['id'], rows[1]['id'])
        self.assertNotEqual(rows[0]['speaker'], rows[1]['speaker'])
        self.assertNotIn('Кандидат', str(rows))

    def test_order_is_stable_explicit_ordinals_survive_shuffled_response(self):
        data = materials()
        first = tb.fingerprint(tb.bundle(data))
        data['recordings'].reverse(); data['transcripts'].reverse()
        self.assertEqual(tb.fingerprint(tb.bundle(data)), first)
        data['recordings'][0]['partIndex'], data['recordings'][1]['partIndex'] = 0, 1
        self.assertNotEqual(tb.fingerprint(tb.bundle(data)), first)

    def test_missing_duplicate_or_foreign_mapping_fails_closed(self):
        for change in ('missing', 'duplicate', 'foreign', 'duplicate_text', 'empty'):
            with self.subTest(change=change):
                data = materials()
                if change == 'missing': data['transcripts'].pop()
                if change == 'duplicate': data['transcripts'][1]['recordingId'] = 'part-a'
                if change == 'foreign': data['transcripts'][1]['recordingId'] = 'other'
                if change == 'duplicate_text': data['transcripts'][1]['id'] = 'text-a'
                if change == 'empty': data['transcripts'][1].update(text='', segments=[])
                with self.assertRaises(Problem): tb.bundle(data)

    def test_pending_does_not_fall_back_to_capture_or_single_legacy(self):
        data = materials(); data['transcription']['status'] = 'processing'
        data['transcriptVariants'] = copy.deepcopy(data['transcripts'])
        with self.assertRaises(Problem): tb.bundle(data, allow_legacy=True)

    def test_legacy_single_only_is_explicitly_opted_in(self):
        data = {'recordings': [{'id': 'one'}], 'transcripts': [{'text': '[00:00-00:02] К: Проверил результат.'}]}
        with self.assertRaises(Problem): tb.bundle(data)
        value = tb.bundle(data, allow_legacy=True)
        self.assertEqual(tb.chunks(value)[0]['speaker'], 'Кандидат')
        data['recordings'].append({'id': 'two'})
        data['transcripts'].append(copy.deepcopy(data['transcripts'][0]))
        with self.assertRaises(Problem): tb.bundle(data, allow_legacy=True)

    def test_any_part_version_content_time_voice_or_set_change_invalidates(self):
        original = tb.fingerprint(tb.bundle(materials()))
        for change in ('id', 'revision', 'text', 'time', 'voice', 'removed'):
            with self.subTest(change=change):
                data = materials(); row = data['transcripts'][1]
                if change == 'id': row['id'] = 'replacement'
                if change == 'revision': row['selectionRevision'] += 1
                if change == 'text': row['text'] += ' новое'
                if change == 'time': row['segments'][0]['end_ms'] += 1
                if change == 'voice': row['segments'][0]['speaker'] = 'speaker_1'
                if change == 'removed': data['recordings'].pop(); data['transcripts'].pop()
                self.assertNotEqual(tb.fingerprint(tb.bundle(data)), original)

    def test_evidence_is_bound_to_exact_text_recording_version_and_local_time(self):
        row = tb.chunks(tb.bundle(materials()))[1]
        evidence = {k: row[k] for k in ('id', 'text', 'speaker', 'recordingId', 'transcriptId')}
        evidence.update(seconds=0, endSeconds=2)
        source = {'inputChunks': [row]}
        tb.check_evidence({'criteria': [{'evidence': [evidence]}]}, source)
        for key in ('id', 'text', 'speaker', 'recordingId', 'transcriptId', 'seconds', 'endSeconds'):
            with self.subTest(key=key):
                tampered = {**evidence, key: 'wrong'}
                with self.assertRaises(Problem): tb.check_evidence({'criteria': [{'evidence': [tampered]}]}, source)

    def test_no_chronology_inferred_when_ordinals_absent(self):
        data = materials()
        for recording in data['recordings']: recording.pop('partIndex')
        data['recordings'].reverse()
        self.assertEqual([p['recordingId'] for p in tb.bundle(data)['parts']], ['part-a', 'part-b'])

    def test_evidence_source_hash_must_attest_exact_submitted_bundle(self):
        row = tb.chunks(tb.bundle(materials()))[0]
        with self.assertRaises(Problem):
            tb.check_evidence({'criteria': [], 'provenance': {'sourceHash': 'wrong'}},
                              {'inputChunks': [row], 'sourceHash': 'expected'})

    def test_timing_is_checked_against_each_own_recording_duration(self):
        data = materials()
        data['recordings'][0]['duration'] = 100
        data['recordings'][1]['duration'] = 1
        with self.assertRaises(Problem): tb.chunks(tb.bundle(data))
        data['recordings'][1]['duration'] = 2
        self.assertEqual(tb.chunks(tb.bundle(data))[1]['endMs'], 2000)

    def test_untimed_segments_are_not_given_fake_timestamps(self):
        data = materials(); data['transcripts'][0]['segments'][0]['start_ms'] = None
        with self.assertRaises(Problem): tb.bundle(data)


if __name__ == '__main__':
    unittest.main()
