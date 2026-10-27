"""Handoff checks bind each conclusion to the same current factual issue."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import json
import tempfile
import unittest

from audit_package import shared_errors
from audit_draft import audit_issue_list
from audit_solo_share import ALLOWED_STATUSES
from route_livestream import build_route, write_route, file_sha256
from build_source_review_index import build_source_review_index
from route_fixture import observations_for


class HandoffGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='handoff synthetic ')
        self.case = Path(self.temp.name)
        for directory in ['inputs', 'outputs', '_work/review_clips']:
            (self.case / directory).mkdir(parents=True)
        self.source = self.case / 'inputs/02_带时间码原稿.md'
        self.source.write_text('发言人1   00:00\n仅用于结构测试。\n\n发言人2   00:50\n合成回应。', encoding='utf-8')
        self.replay = self.case / 'inputs/01_直播回放.wav'
        self.replay.write_bytes(b'synthetic media fixture, not real speech')
        write_route(self.case, build_route(self.case, acoustic_speakers='2', evidence=[str(i) for i in range(5)], observations=observations_for(self.case)))
        calibration = self.case / '_work/说话人校准.json'
        calibration.write_text('{}', encoding='utf-8')
        (self.case / '_work/说话人时间轴.json').write_text(json.dumps({'provenance': {
            'replay_sha256': file_sha256(self.replay),
            'transcript_sha256': file_sha256(self.source),
            'calibration_sha256': file_sha256(calibration),
        }}), encoding='utf-8')
        self.issues = self.case / 'outputs/02_待核问题清单.md'
        self.draft = self.case / 'outputs/01_公众号可发布稿.md'
        self.draft.write_text('主持人：后来做了一段时间。', encoding='utf-8')
        self.set_issue('姓名存在疑问', '回听后确认使用台卡姓名', '已回听')
        self.manifest = {'source_sha256': file_sha256(self.replay),
                         'transcript_sha256': file_sha256(self.source),
                         'clips': [], 'samples': []}
        for index in range(6):
            name = 'P1-01-01-000002.m4a' if index == 0 else f'SAMPLE-{index:02d}-0000{index:02d}.m4a'
            clip = self.case / '_work/review_clips' / name
            clip.write_bytes(('synthetic clip ' + str(index)).encode())
            item = {'priority': 'P1' if index == 0 else 'SAMPLE',
                    'issue_index': 1 if index == 0 else index,
                    'timecode': '00:10' if index == 0 else f'00:{index:02d}',
                    'issue_type': '人名' if index == 0 else '随机抽听',
                    'issue_text': '姓名存在疑问' if index == 0 else '',
                    'filename': name, 'audio_sha256': file_sha256(clip)}
            self.manifest['clips' if index == 0 else 'samples'].append(item)
        self.write_manifest()
        self.sheet = self.case / '_work/回听终审表.md'
        self.write_sheet()

    def tearDown(self):
        self.temp.cleanup()

    def set_issue(self, text, action, status):
        self.issues.write_text('| 级别 | 时间码 | 问题类型 | 原文/成稿 | 处理建议 | 状态 |\n'
                               '| --- | --- | --- | --- | --- | --- |\n'
                               f'| P1 | 00:10 | 人名 | {text} | {action} | {status} |\n\n'
                               '- P0：0\n- P1：0 项待确认\n- P2：0\n', encoding='utf-8')
        build_source_review_index(self.case)

    def write_manifest(self):
        (self.case / '_work/review_clips/manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def write_sheet(self, signer='编辑甲 / 2026-10-22', conclusion='已回听确认台卡姓名'):
        lines = ['# 合成终审结构测试',
                 f'| 1 | 00:10 | 人名：姓名存在疑问 | `P1-01-01-000002.m4a` | 听到的姓名已记录 | 已采用核验写法 | {signer} |']
        lines += [f"| {item['timecode']} | `{item['filename']}` | {conclusion} | 否 | {signer} |"
                  for item in self.manifest['samples']]
        self.sheet.write_text('\n'.join(lines), encoding='utf-8')

    def errors(self):
        return shared_errors(self.case, handoff=True)[1]

    def test_status_only_change_keeps_legitimate_review_binding(self):
        self.assertEqual(self.errors(), [])

    def test_packaged_human_readable_issue_templates_are_auditable(self):
        root = Path(__file__).resolve().parents[1]
        for folder in ['templates', 'solo']:
            with self.subTest(mode=folder):
                path = root/'assets'/folder/'02_待核问题清单.md'
                text = path.read_text(encoding='utf-8')
                self.assertFalse([f for f in audit_issue_list(text, path) if f.level == 'ERROR'])

    def test_closed_issue_keeps_trace_without_becoming_an_open_count(self):
        root = Path(__file__).resolve().parents[1]
        path = root/'assets/templates/02_待核问题清单.md'
        text = path.read_text(encoding='utf-8').replace('<details>',
            '| P1 | 00:10 | 数字 | 原文存疑 | 正文采用“后来做了一段时间” | 已保守表达 |\n\n<details>', 1)
        self.assertFalse([f for f in audit_issue_list(text, path) if f.level == 'ERROR'])

    def test_unparsed_source_cannot_be_routed_as_ready_to_edit(self):
        self.source.write_text('只有摘要，没有可追溯的时间块。', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '时间块'):
            build_route(self.case, acoustic_speakers='1', evidence=[str(i) for i in range(5)])
        self.assertEqual(build_route(self.case, acoustic_speakers='uncertain', evidence=['无法确认'])['status'], 'BLOCKED')

    def test_generic_resolved_status_cannot_complete_handoff(self):
        self.set_issue('姓名存在疑问', '已经处理好了', '已解决')
        self.assertTrue(any('明确关闭方式' in e for e in self.errors()))

    def test_changed_issue_cannot_reuse_old_clip_and_signoff(self):
        self.set_issue('另一个人的收入数字存在疑问', '沿用先前结论', '已回听')
        self.assertTrue(any('当前待核问题' in e for e in self.errors()))

    def test_changed_sheet_question_cannot_keep_same_filename_and_signoff(self):
        text = self.sheet.read_text(encoding='utf-8').replace('人名：姓名存在疑问', '收入：另一项问题')
        self.sheet.write_text(text, encoding='utf-8')
        self.assertTrue(any('核验内容' in e for e in self.errors()))

    def test_listening_claim_requires_that_issue_in_clip_manifest(self):
        self.manifest['clips'] = []
        self.write_manifest()
        self.assertTrue(any('对应的回听音频' in e for e in self.errors()))

    def test_conservative_status_is_supported_and_must_match_current_draft(self):
        self.assertIn('已保守表达', ALLOWED_STATUSES)
        self.set_issue('姓名存在疑问', '正文采用“后来做了一段时间”', '已保守表达')
        self.assertEqual(self.errors(), [])
        self.assertFalse(any(f.code == 'ISSUE_STATUS' for f in audit_issue_list(self.issues.read_text(), self.issues)))
        self.draft.write_text('主持人：正文并未采用所记录的保守表达。', encoding='utf-8')
        self.assertTrue(any('最终保守原句' in e for e in self.errors()))

    def test_date_alone_is_not_a_human_signature(self):
        self.write_sheet(signer='2026-10-22')
        self.assertTrue(any('真人签字' in e for e in self.errors()))

    def test_unheard_or_invalid_calendar_date_cannot_complete_review(self):
        self.write_sheet(conclusion='未回听')
        self.assertTrue(any('回听结论' in e for e in self.errors()))
        self.write_sheet(signer='编辑甲 / 2026-99-99')
        self.assertTrue(any('真人签字' in e for e in self.errors()))


if __name__ == '__main__':
    unittest.main()
