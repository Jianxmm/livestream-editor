"""Personalization never replaces case evidence or overwrites human edits."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import json
import tempfile
import unittest

from personal_profile import (ROOT, PROFILE_FILES, init_profile, inspect_profile,
                              confirmed_rows, snapshot_profile, validate_snapshot)
from workspace import init_case
from build_release import release_files
from route_livestream import build_route, write_route
from build_source_review_index import build_source_review_index
from audit_package import shared_errors


class PersonalProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='个人资料测试 ')
        self.root = Path(self.temp.name)
        self.profile = self.root / '我的文稿资料'
        init_profile(self.profile)
        self.media = self.root / 'source.wav'
        self.media.write_bytes(b'synthetic media only')
        self.transcript = self.root / '原稿.md'
        self.transcript.write_text('发言人1   00:00\n合成测试，不是真实讲话。\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def terms(self):
        with (self.profile / '01_专用词库.md').open('a', encoding='utf-8') as stream:
            stream.write('| 示例品牌 | 品牌 | 示例品排 | — | 已确认 | 合成测试的明确确认 |\n')
            stream.write('| 待核名称 | 人名 | 错别名 | — | 待确认 | 尚未核实 |\n')

    def test_init_is_optional_blank_and_preserves_human_edits(self):
        self.assertEqual(inspect_profile(self.profile)['confirmed_terms'], 0)
        path = self.profile / '02_参考案例与偏好.md'
        path.write_text('人工确认的偏好，不可覆盖。', encoding='utf-8')
        (self.profile / 'README.md').unlink()
        result = init_profile(self.profile)
        self.assertEqual(result['created'], ['README.md'])
        self.assertEqual(path.read_text(encoding='utf-8'), '人工确认的偏好，不可覆盖。')

    def test_profile_cannot_be_kept_inside_installation(self):
        with self.assertRaises(ValueError):
            init_profile(ROOT / '我的文稿资料')
        with self.assertRaises(ValueError):
            inspect_profile(ROOT)

    def test_symlink_cannot_smuggle_private_files(self):
        path = self.profile / '01_专用词库.md'
        path.unlink()
        try:
            path.symlink_to(self.transcript)
        except OSError:
            self.skipTest('platform does not allow unprivileged symlinks')
        with self.assertRaises(ValueError):
            init_profile(self.profile)
        with self.assertRaises(ValueError):
            inspect_profile(self.profile)

    def test_confirmed_terms_need_evidence_and_consistent_aliases(self):
        with self.assertRaises(ValueError):
            confirmed_rows('| 甲 | 品牌 | 别字 | — | 已确认 | 待补充 |')
        with self.assertRaises(ValueError):
            confirmed_rows('| 甲 | 品牌 | 别字 | — | 已确认 | 用户确认 |\n'
                           '| 乙 | 品牌 | 别字 | — | 已确认 | 用户确认 |')
        self.assertEqual(confirmed_rows('| 甲 | 人名 | 乙 | — | 待确认 | 猜测 |'), [])

    def test_new_case_imports_only_confirmed_terms_and_preserves_sources(self):
        self.terms()
        case = init_case(self.root / '案例', self.media, self.transcript, profile=self.profile)
        terms = (case / 'inputs/03_人物与专名表.md').read_text(encoding='utf-8')
        self.assertIn('示例品牌', terms)
        self.assertNotIn('待核名称', terms)
        self.assertEqual((case / 'inputs/02_带时间码原稿.md').read_bytes(), self.transcript.read_bytes())
        self.assertEqual((case / 'inputs/01_直播回放.wav').read_bytes(), self.media.read_bytes())
        self.assertEqual(validate_snapshot(case), [])

    def test_explicit_case_terms_are_not_overwritten_by_long_term_profile(self):
        self.terms()
        people = self.root / '本场确认.md'
        people.write_bytes(b'\xef\xbb\xbf# Case-specific terms\r\n')
        case = init_case(self.root / '案例', self.media, self.transcript, people, self.profile)
        self.assertEqual(people.read_bytes(), (case / 'inputs/03_人物与专名表.md').read_bytes())
        self.assertEqual(validate_snapshot(case), [])

    def test_later_profile_changes_do_not_rewrite_case_snapshot(self):
        case = init_case(self.root / '案例', self.media, self.transcript, profile=self.profile)
        frozen = (case / '_work/个人资料/02_参考案例与偏好.md').read_bytes()
        (self.profile / '02_参考案例与偏好.md').write_text('后来改变的偏好', encoding='utf-8')
        self.assertEqual(validate_snapshot(case), [])
        self.assertEqual((case / '_work/个人资料/02_参考案例与偏好.md').read_bytes(), frozen)
        record = json.loads((case / '_work/个人资料使用记录.json').read_text(encoding='utf-8'))
        self.assertEqual(record['source_directory'], str(self.profile.resolve()))
        with self.assertRaises(FileExistsError):
            snapshot_profile(case, self.profile)

    def test_snapshot_change_is_rejected_by_real_package_audit(self):
        case = init_case(self.root / '案例', self.media, self.transcript, profile=self.profile)
        write_route(case, build_route(case, acoustic_speakers='1', evidence=[str(i) for i in range(5)]))
        (case / 'outputs/02_待核问题清单.md').write_text('# 无待核问题', encoding='utf-8')
        build_source_review_index(case)
        self.assertEqual(shared_errors(case), ('solo', []))
        (case / '_work/个人资料/02_参考案例与偏好.md').write_text('改动', encoding='utf-8')
        self.assertTrue(any('个人资料' in e for e in shared_errors(case)[1]))

    def test_invalid_profile_leaves_no_partial_case(self):
        (self.profile / '01_专用词库.md').write_text('| 甲 | 人名 | — | — | 已确认 | — |', encoding='utf-8')
        case = self.root / '不应生成'
        with self.assertRaises(ValueError):
            init_case(case, self.media, self.transcript, profile=self.profile)
        self.assertFalse(case.exists())

    def test_distribution_rejects_real_profile_even_if_allowlisted(self):
        private = self.root / '我的文稿资料/01_专用词库.md'
        fake = self.root / 'release-files.json'
        fake.write_text(json.dumps(['我的文稿资料/01_专用词库.md']), encoding='utf-8')
        self.assertTrue(private.is_file())
        with self.assertRaisesRegex(ValueError, '不允许的分发路径'):
            release_files(self.root)

    def test_historical_case_without_personalization_remains_optional(self):
        case = init_case(self.root / '无个人资料', self.media, self.transcript)
        self.assertEqual(validate_snapshot(case), [])
        self.assertFalse((case / '_work/个人资料').exists())
        self.assertEqual(set(PROFILE_FILES), {p.name for p in self.profile.iterdir()})


if __name__ == '__main__':
    unittest.main()
