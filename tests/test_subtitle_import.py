"""Preserve every caption and reject conversion that cannot retain source timing."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from subtitle_import import convert_subtitles
from workspace import init_case, inspect_case, input_manifest_errors, templates, subtitle_timing_errors
from route_livestream import build_route, file_sha256, valid_default_map
from speaker_pipeline import align_blocks, validate_calibration
from build_source_review_index import build_source_review_index
from audit_package import shared_errors

SRT = '1\r\n00:00:00,120 --> 00:00:01,800\r\n第一行，保留。\r\n第二行 &amp; 数字123。\r\n\r\n2\r\n00:00:02,900 --> 00:00:04,300\r\n<b>原样的观点</b>\r\n'


class SubtitleTests(unittest.TestCase):
    def test_srt_preserves_multiline_words_exact_times_and_unverified_identity(self):
        text, record = convert_subtitles(SRT.encode('utf-8-sig'), 'srt')
        self.assertIn('发言人未标注   00:00:00\n第一行，保留。\n第二行 & 数字123。', text)
        self.assertIn('原样的观点', text)
        self.assertEqual(record['cues'][1]['start_ms'], 2900)
        self.assertEqual(record['cues'][1]['end_ms'], 4300)
        self.assertIn('<b>', record['cues'][1]['raw_text'])
        self.assertEqual(record['voice_labels'], {})

    def test_vtt_voice_labels_metadata_and_settings_are_preserved(self):
        data = 'WEBVTT\n\nNOTE export note\nnot speech\n\nSTYLE\n::cue { color: red }\n\nintro\n00:00.100 --> 00:01.900 align:start\n<v 小甲>你好 <i>世界</i>\n\n00:02.000 --> 00:04.000\n<v 小乙>谢谢。'
        text, record = convert_subtitles(data.encode(), 'vtt')
        self.assertIn('发言人1   00:00:00\n你好 世界', text)
        self.assertIn('发言人2   00:00:02', text)
        self.assertNotIn('not speech', text)
        self.assertEqual(record['voice_labels'], {'小甲': '发言人1', '小乙': '发言人2'})
        self.assertEqual(record['cues'][0]['settings'], 'align:start')
        self.assertIn('未核验', record['speaker_status'])

    def test_utf16_bom_supported_without_encoding_guesses(self):
        text, record = convert_subtitles(SRT.encode('utf-16'), 'srt')
        self.assertIn('第一行', text)
        self.assertEqual(record['encoding'], 'utf-16')
        with self.assertRaisesRegex(ValueError, '编码'):
            convert_subtitles(SRT.encode('gb18030'), 'srt')

    def test_bad_or_unsupported_subtitles_fail_with_location(self):
        samples = [('', 'srt'), ('not subtitles', 'vtt'),
                   ('1\n00:00:00,000 --> 00:00:00,000\n文字', 'srt'),
                   ('1\n00:60:00,000 --> 01:01:00,000\n文字', 'srt'),
                   ('1\n00:00:00.000 --> 00:00:01.000\n文字', 'srt'),
                   ('1\n00:00:00,000 --> 00:00:01,000', 'srt'),
                   ('WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0\n\n00:00.000 --> 00:01.000\n文字', 'vtt')]
        for data, kind in samples:
            with self.subTest(data=data), self.assertRaises(ValueError):
                convert_subtitles(data.encode(), kind)

    def test_overlap_rolling_and_same_second_cannot_be_silently_merged(self):
        for second in [('00:00:01,500', '00:00:03,000'), ('00:00:00,800', '00:00:01,000')]:
            end = '00:00:02,000' if second[0].endswith('500') else '00:00:00,700'
            data = f'1\n00:00:00,100 --> {end}\n甲\n\n2\n{second[0]} --> {second[1]}\n乙'
            with self.subTest(second=second), self.assertRaises(ValueError):
                convert_subtitles(data.encode(), 'srt')

    def test_multivoice_ruby_or_transcript_injection_rejected(self):
        for payload in ['<v 甲>甲<v 乙>乙', '甲<v 乙>乙', '<v 甲>甲</v>乙', '<ruby>字<rt>zi</rt></ruby>', '发言人2   00:10\n另一个时间行']:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                convert_subtitles(('WEBVTT\n\n00:00.000 --> 00:01.000\n' + payload).encode(), 'vtt')

    def test_extra_blank_lines_and_literal_angle_brackets_do_not_lose_text(self):
        text, record = convert_subtitles(SRT.replace('\r\n\r\n', '\r\n\r\n\r\n').replace('<b>原样的观点</b>', 'x < 3，&lt;保留&gt;').encode(), 'srt')
        self.assertIn('x < 3，<保留>', text)
        self.assertEqual(record['cue_count'], 2)

    def test_one_malformed_late_block_rejects_whole_input(self):
        with self.assertRaises(ValueError):
            convert_subtitles((SRT + '\r\n3\r\n坏时间码\r\n最后一句不能被忽略').encode(), 'srt')

    def test_init_copies_original_and_binds_conversion_to_audit(self):
        with tempfile.TemporaryDirectory(prefix='字幕 中文 ') as directory:
            root = Path(directory)
            media = root / '视频.wav'; media.write_bytes(b'synthetic audio')
            original = root / '字幕.SRT'; original.write_bytes(SRT.encode('utf-8-sig'))
            before = file_sha256(original)
            case = init_case(root / '案例', media, original)
            self.assertEqual(file_sha256(case / 'inputs/05_平台原始导出.srt'), before)
            self.assertEqual(file_sha256(original), before)
            with patch('workspace.duration', return_value=5):
                result = inspect_case(case)
            self.assertEqual(result['errors'], [])
            self.assertTrue(any('标签' in w for w in result['warnings']))
            route = build_route(case, acoustic_speakers='2', evidence=[f'真实核验占位仅用于测试{i}' for i in range(5)])
            self.assertEqual(route['mode'], 'dual')
            self.assertTrue(any('未标注' in warning for warning in route['warnings']))
            (case / '_work/场次分流.json').write_text(json.dumps(route), encoding='utf-8')
            templates(case, 'dual')
            calibration = json.loads((case / '_work/说话人校准.json').read_text(encoding='utf-8'))
            self.assertEqual(calibration['source_label_default_map'], {'发言人未标注': None})
            (case / 'outputs/02_待核问题清单.md').write_text('# 测试\n无问题', encoding='utf-8')
            build_source_review_index(case)
            for name in ['inputs/05_平台原始导出.srt', '_work/字幕导入记录.json']:
                path = case / name; old = path.read_bytes(); path.write_bytes(b'changed')
                with patch('workspace.duration', return_value=5):
                    self.assertTrue(any(name in e for e in shared_errors(case)[1]))
                path.write_bytes(old)
            self.assertEqual(input_manifest_errors(case), [])
            (case / '_work/输入来源.json').unlink()
            self.assertTrue(any('缺少输入来源' in e for e in input_manifest_errors(case)))

    def test_unlabelled_dual_input_requires_explicit_conflicts_instead_of_guessed_identity(self):
        mapping = {'发言人未标注': None}
        self.assertTrue(valid_default_map(mapping, {'A', 'B'}, {'发言人未标注'}))
        self.assertFalse(valid_default_map({'发言人未标注': 'A'}, {'A', 'B'}))
        self.assertFalse(valid_default_map({'发言人1': 'A'}, {'A', 'B'}))
        self.assertFalse(valid_default_map(mapping, {'A', 'B'}, {'发言人1'}))
        people = [{'speaker_id': sid, 'name': name, 'reference_segments': [
            {'start': start, 'end': end, 'identity_basis': '真人根据原回放自我介绍核实',
             'review_status': '已回听', 'overlap': False}
            for start, end in ranges
        ]} for sid, name, ranges in [('A', '甲', [('00:00', '00:15'), ('00:15', '00:30')]),
                                    ('B', '乙', [('00:30', '00:45'), ('00:45', '01:00')])]]
        validate_calibration({'status': '已确认', 'speakers': people, 'source_label_default_map': mapping}, 70)
        segments = [{'id': 's1', 'start_seconds': 0, 'end_seconds': 10, 'speaker_id': 'A', 'uncertain': False, 'overlap': False}]
        blocks, conflicts = align_blocks('发言人未标注 00:00\n原话。', segments, mapping, 10)
        self.assertEqual(blocks[0]['status'], '冲突待核')
        self.assertEqual(len(conflicts), 1)

    def test_caption_end_beyond_media_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / 'test.wav'; media.write_bytes(b'synthetic')
            subtitle = root / 'test.srt'; subtitle.write_text('1\n00:00:00,100 --> 00:01:00,000\n很长的字幕', encoding='utf-8')
            case = init_case(root / 'case', media, subtitle)
            with patch('workspace.duration', return_value=10):
                self.assertTrue(any('结束时间' in e for e in inspect_case(case)['errors']))
                self.assertTrue(any('结束时间' in e for e in shared_errors(case)[1]))

    def test_bad_subtitle_leaves_no_partial_case(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / 'test.wav'; media.write_bytes(b'synthetic')
            subtitle = root / 'test.srt'; subtitle.write_text(SRT + '\n坏块', encoding='utf-8')
            with self.assertRaises(ValueError):
                init_case(root / 'case', media, subtitle)
            self.assertFalse((root / 'case').exists())


if __name__ == '__main__':
    unittest.main()
