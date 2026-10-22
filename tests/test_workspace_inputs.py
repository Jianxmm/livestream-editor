"""Validate real input paths and timestamps before any editing starts."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import tempfile
import contextlib
import io
import unittest
from unittest.mock import patch

from workspace import init_case, inspect_case
from personal_profile import init_profile


class WorkspaceInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='input contract ')
        self.root = Path(self.temp.name)
        self.media = self.root / 'media.wav'; self.media.write_bytes(b'synthetic media')
        self.source = self.root / 'source.md'
        self.source.write_text('发言人1   00:00\n合成测试。\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_expanded_people_path_is_the_path_actually_copied(self):
        people = self.root / '人物.md'; people.write_bytes(b'\xef\xbb\xbf# Approved terms\r\n')
        expand = Path.expanduser
        with patch.object(Path, 'expanduser', lambda p: people if p.as_posix() == '~/人物.md' else expand(p)):
            case = init_case(self.root / 'case', self.media, self.source, Path('~/人物.md'))
        self.assertEqual(people.read_bytes(), (case / 'inputs/03_人物与专名表.md').read_bytes())

    def test_elapsed_minutes_above_59_are_valid(self):
        self.source.write_text('发言人1   00:00\n开场。\n\n发言人1   70:00\n七十分。\n', encoding='utf-8')
        case = init_case(self.root / 'case', self.media, self.source)
        with patch('workspace.duration', return_value=5000):
            self.assertEqual(inspect_case(case)['errors'], [])

    def test_invalid_or_regressing_times_leave_no_case(self):
        for index, times in enumerate([('00:75',), ('00:30', '00:20'), ('01:70:00',)]):
            with self.subTest(times=times):
                self.source.write_text('\n\n'.join(f'发言人1   {t}\n合成内容。' for t in times), encoding='utf-8')
                case = self.root / f'bad-{index}'
                with self.assertRaises(ValueError):
                    init_case(case, self.media, self.source)
                self.assertFalse(case.exists())

    def test_people_copy_corruption_cannot_complete_initialization(self):
        import shutil
        people = self.root / 'people.md'; people.write_bytes(b'approved people')
        copy = shutil.copyfile
        def corrupt(src, dst):
            result = copy(src, dst)
            if Path(src).resolve() == people.resolve():
                Path(dst).write_bytes(b'corrupted')
            return result
        case = self.root / 'bad-copy'
        with patch('workspace.shutil.copyfile', side_effect=corrupt), self.assertRaises(RuntimeError):
            init_case(case, self.media, self.source, people)
        self.assertFalse(case.exists())
        self.assertEqual(people.read_bytes(), b'approved people')

    def test_resuming_case_checks_personal_material_snapshot(self):
        profile = self.root / '我的文稿资料'; init_profile(profile)
        case = init_case(self.root / 'case', self.media, self.source, profile=profile)
        (case / '_work/个人资料/02_参考案例与偏好.md').write_text('后来修改', encoding='utf-8')
        with patch('workspace.duration', return_value=30):
            self.assertTrue(any('个人资料' in e for e in inspect_case(case)['errors']))

    def test_self_test_help_does_not_execute_media_checks(self):
        import self_test
        with contextlib.redirect_stdout(io.StringIO()), patch.object(sys, 'argv', ['self_test.py', '--help']), patch.object(self_test, 'smoke') as smoke:
            with self.assertRaises(SystemExit) as result:
                self_test.main()
            self.assertEqual(result.exception.code, 0)
            smoke.assert_not_called()


if __name__ == '__main__':
    unittest.main()
