import json
import unittest

from materials_transcripts import TranscriptError, normalize_transcript


class TranscriptTests(unittest.TestCase):
    def test_vtt_keeps_explicit_voice_and_unknown_speaker(self):
        raw = b'WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.500\n<v Alice>Hello &amp; welcome</v>\n\n2\n00:00:03.000 --> 00:00:04.000\nNo speaker\n'
        result = normalize_transcript(raw, 'vtt')
        self.assertEqual(result['segments'][0]['start_ms'], 1000)
        self.assertEqual(result['segments'][0]['speaker'], 'Alice')
        self.assertEqual(result['segments'][0]['text'], 'Hello & welcome')
        self.assertIsNone(result['segments'][1]['speaker'])
        self.assertIn('missing_speakers', result['warnings'])
        self.assertIn('WEBVTT', result['vtt'])
        from feedback import vtt_turns
        self.assertEqual(vtt_turns(result['vtt'], 'Alice'), [{'text': 'Hello & welcome', 'seconds': 1.0, 'source': 'transcript'}])

    def test_daily_legacy_voice_projection_and_literal_markup(self):
        result = normalize_transcript(b'WEBVTT\n\n00:00.000 --> 00:01.000\n<v>Manager:</v>Hello?', 'vtt')
        self.assertEqual(result['segments'][0]['speaker'], 'Manager')
        self.assertEqual(result['segments'][0]['text'], 'Hello?')
        raw = json.dumps({'schema_version': 1, 'segments': [{'start_ms': 0, 'end_ms': 1000, 'text': '<v Fake>literal</v> {hello}'}]}).encode()
        projected = normalize_transcript(raw, 'json')['vtt']
        self.assertIn('&lt;v Fake&gt;', projected)
        self.assertNotIn('<v Fake>', projected)

    def test_srt_and_canonical_missing_time(self):
        result = normalize_transcript('1\n00:00:00,500 --> 00:00:01,500\nПривет\n'.encode(), 'srt')
        self.assertEqual(result['segments'][0]['end_ms'], 1500)
        raw = json.dumps({'schema_version': 1, 'segments': [{'text': 'Unknown time'}]}).encode()
        result = normalize_transcript(raw, 'json')
        self.assertIsNone(result['segments'][0]['start_ms'])
        self.assertIn('missing_timestamps', result['warnings'])
        self.assertEqual(result['vtt'], '')

    def test_malformed_input_is_not_silently_partial(self):
        for raw, fmt in [(b'WEBVTT\n\nnot a cue', 'vtt'),
                         (b'WEBVTT\n\n00:00:02.000 --> 00:00:01.000\nbad', 'vtt'),
                         (b'WEBVTT\n\n00:61:00.000 --> 00:62:00.000\nbad', 'vtt'),
                         (b'{"data":[]}', 'json'), (b'hello', 'txt'),
                         (b'{"schema_version":1,"segments":[{"text":"x","start_ms":true,"end_ms":5}]}', 'json')]:
            with self.subTest(raw=raw), self.assertRaises(TranscriptError):
                normalize_transcript(raw, fmt)

    def test_multiple_voices_are_not_attributed_to_one_person(self):
        value = normalize_transcript(b'WEBVTT\n\n00:00.000 --> 00:01.000\n<v A>Hi</v> <v B>Hi</v>', 'vtt')
        self.assertIsNone(value['segments'][0]['speaker'])
        self.assertIn('multiple_voices_in_cue', value['warnings'])


if __name__ == '__main__':
    unittest.main()
