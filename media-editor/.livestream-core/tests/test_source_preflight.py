import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
from source_preflight import inspect_source, main


class TranscriptPreflightTest(unittest.TestCase):
    def test_timed_start_only_never_becomes_a_claim_of_missing_speech(self):
        text = '发言人1   00:00\n这是一次长时间的单人分享。\n\n发言人1   09:55\n结尾。\n'
        report = inspect_source(text, 600)
        self.assertIsNone(report['timed_gap_candidates'])
        self.assertIn('不能', report['gap_note'])
        self.assertEqual(report['source_turn_count'], 2)
        self.assertEqual([s['position'] for s in report['samples']], ['开头','中段','结尾'])

    def test_subtitle_middle_gap_is_a_candidate_even_when_end_matches(self):
        text = '发言人1   00:00\n前段。\n\n发言人1   09:30\n后段。\n'
        cues = [{'start_ms':0,'end_ms':30000}, {'start_ms':570000,'end_ms':600000}]
        result = inspect_source(text, 600, cues)
        self.assertEqual(result['timed_gap_candidates'], [{'start_seconds':30,'end_seconds':570}])
        self.assertIn('静音', result['gap_note'])
        self.assertIn('未完成回听', result['status'])

    def test_overlap_and_repeated_wording_are_not_deleted_or_called_missing(self):
        phrase = '我们每一天都在认真的学习和练习这个方法'
        text = '发言人1   00:00\n'+phrase+'。\n\n发言人2   00:05\n'+phrase+'。\n'
        cues = [{'start_ms':0,'end_ms':8000}, {'start_ms':5000,'end_ms':12000}]
        result = inspect_source(text, 12, cues)
        self.assertEqual(result['timed_gap_candidates'], [])
        self.assertEqual(len(result['repeated_text_candidates']), 1)
        self.assertEqual(result['source_turn_count'],2)
        self.assertIn('不自动删字',result['review_note'])

    def test_real_case_preflight_is_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            case=Path(d); inputs=case/'inputs';inputs.mkdir()
            raw='发言人1   00:00\n真实输入应保持原样。\n'.encode()
            source=inputs/'02_带时间码原稿.md';source.write_bytes(raw)
            with wave.open(str(inputs/'01_直播回放.wav'),'wb') as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*8000)
            before={str(p.relative_to(case)):hashlib.sha256(p.read_bytes()).hexdigest() for p in case.rglob('*') if p.is_file()}
            old=sys.argv; sys.argv=['source_preflight.py',str(case)]
            try:
                with contextlib.redirect_stdout(io.StringIO()) as result:self.assertEqual(main(),0)
            finally:sys.argv=old
            value=json.loads(result.getvalue());self.assertEqual(value['source_sha256'],hashlib.sha256(raw).hexdigest())
            after={str(p.relative_to(case)):hashlib.sha256(p.read_bytes()).hexdigest() for p in case.rglob('*') if p.is_file()}
            self.assertEqual(before,after)


if __name__=='__main__':unittest.main()
