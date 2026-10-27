"""A reading copy must work without the source case and preserve human edits."""
from pathlib import Path
import hashlib
import json
import re
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workspace import export_outputs


class ReadingExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='阅读导出 fixture ')
        self.root = Path(self.temp.name).resolve()
        self.case = self.root / '源案例'
        self.output = self.case / 'outputs'
        self.work = self.case / '_work'
        self.output.mkdir(parents=True)
        (self.work / 'review_clips').mkdir(parents=True)
        self.destination = self.root / '独立 阅读副本'
        self.write(self.output / '01_公众号可发布稿.md', '# 合成正文\n\n逐字保留正文。\n')
        self.write(self.output / '02_待核问题清单.md', '# 合成问题\n\n[00:10](../_work/待核原文索引.md#review-001)\n')
        self.write(self.output / '03_编辑质检报告.md', '# 合成说明\n')
        self.write(self.work / '待核原文索引.md', '# 合成原文\n\n<a id="review-001"></a>\n原话摘录。\n\n[返回问题](../outputs/02_待核问题清单.md)\n')
        self.write(self.work / '私人身份与配置.json', '{"private": true}')
        (self.case / 'inputs').mkdir()
        (self.case / 'inputs/01_直播回放.mp4').write_bytes(b'do not copy whole replay')

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8')

    def add_audio(self, *, index=True):
        audio = self.work / 'review_clips/P1-01.m4a'
        audio.write_bytes(b'synthetic review audio')
        self.write(self.work / '回听终审表.md', '# 合成记录\n`P1-01.m4a`：待核\n')
        manifest = {'version': 1, 'clips': [{'filename': audio.name, 'priority': 'P1', 'timecode': '00:10',
                    'issue_type': '合成问题', 'issue_text': '待核原话', 'audio_sha256': hashlib.sha256(audio.read_bytes()).hexdigest()}], 'samples': []}
        self.write(self.work / 'review_clips/manifest.json', json.dumps(manifest, ensure_ascii=False))
        if index:
            self.write(self.work / '回听辅助索引.md', '# 合成音频入口\n\n[打开音频](review_clips/P1-01.m4a)\n\n<audio controls src="review_clips/P1-01.m4a"></audio>\n')
        return audio

    def assert_local_links_exist(self, destination):
        for path in destination.rglob('*.md'):
            text = path.read_text(encoding='utf-8-sig')
            targets = re.findall(r'\[[^\]]*\]\(([^)]+)\)', text) + re.findall(r'\bsrc=["\']([^"\']+)["\']', text)
            for target in targets:
                parts = urlsplit(target.strip('<>'))
                if parts.scheme or parts.netloc or not parts.path:
                    continue
                resolved = (path.parent / unquote(parts.path)).resolve()
                self.assertTrue(resolved.is_relative_to(destination.resolve()), (path, target))
                self.assertTrue(resolved.is_file(), (path, target))

    def test_reading_package_is_independent_and_does_not_copy_unrelated_private_files(self):
        self.add_audio()
        before = {p.relative_to(self.case).as_posix(): p.read_bytes() for p in self.case.rglob('*') if p.is_file()}
        result = export_outputs(self.case, self.destination)
        self.assertEqual(result, self.destination)
        self.assertEqual((result / '01_公众号可发布稿.md').read_bytes(), before['outputs/01_公众号可发布稿.md'])
        self.assertEqual(before, {p.relative_to(self.case).as_posix(): p.read_bytes() for p in self.case.rglob('*') if p.is_file()})
        self.case.rename(self.root / '原案例已移走')
        self.assert_local_links_exist(result)
        self.assertTrue(list(result.rglob('*.m4a')))
        self.assertFalse(list(result.rglob('*.mp4')))
        self.assertFalse(list(result.rglob('manifest.json')))
        self.assertFalse(list(result.rglob('私人身份与配置.json')))
        self.assertIn('核对', (result / '00_从这里开始.md').read_text())

    def test_manifest_supplies_audio_entry_when_source_helper_index_not_yet_generated(self):
        self.add_audio(index=False)
        result = export_outputs(self.case, self.destination)
        self.assert_local_links_exist(result)
        entry = (result / '00_从这里开始.md').read_text()
        self.assertIn('回听', entry)
        self.assertTrue(list(result.rglob('*.m4a')))
        self.assertFalse((self.work / '回听辅助索引.md').exists())

    def test_missing_referenced_material_leaves_no_partial_destination(self):
        (self.work / '待核原文索引.md').unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            export_outputs(self.case, self.destination)
        self.assertFalse(self.destination.exists())

    def test_missing_or_changed_review_audio_is_rejected(self):
        audio = self.add_audio(index=False)
        audio.write_bytes(b'changed after manifest')
        with self.assertRaises(ValueError):
            export_outputs(self.case, self.destination)
        self.assertFalse(self.destination.exists())
        audio.unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            export_outputs(self.case, self.destination)
        self.assertFalse(self.destination.exists())

    def test_links_cannot_copy_arbitrary_inputs_or_leave_case(self):
        for target in ['../inputs/01_直播回放.mp4', '../../outside.txt', '../_work/私人身份与配置.json']:
            self.write(self.output / '02_待核问题清单.md', f'[不应导出]({target})\n')
            with self.subTest(target=target), self.assertRaises(ValueError):
                export_outputs(self.case, self.destination)
            self.assertFalse(self.destination.exists())

    def test_referenced_symlinks_are_rejected(self):
        target = self.work / '待核原文索引.md'
        target.unlink()
        outside = self.root / 'outside.md'
        self.write(outside, '# unrelated')
        try:
            target.symlink_to(outside)
        except OSError:
            self.skipTest('symbolic links unavailable')
        with self.assertRaises(ValueError):
            export_outputs(self.case, self.destination)
        self.assertFalse(self.destination.exists())

    def test_same_export_reuses_copy_and_human_changes_are_not_overwritten(self):
        self.add_audio()
        first = export_outputs(self.case, self.destination)
        before = {p.relative_to(first).as_posix(): p.read_bytes() for p in first.rglob('*') if p.is_file()}
        self.assertEqual(first, export_outputs(self.case, first))
        self.assertEqual(before, {p.relative_to(first).as_posix(): p.read_bytes() for p in first.rglob('*') if p.is_file()})
        manual = first / '01_公众号可发布稿.md'
        self.write(manual, '# 真人改稿\n必须保留。')
        with self.assertRaises(FileExistsError):
            export_outputs(self.case, first)
        self.assertEqual(manual.read_text(), '# 真人改稿\n必须保留。')

    def test_encoded_audio_paths_and_external_links_remain_usable(self):
        audio = self.work / 'review_clips/中文 audio.m4a'
        audio.write_bytes(b'synthetic audio')
        self.write(self.work / '回听辅助索引.md', '# 音频\n\n[片段](<review_clips/中文%20audio.m4a>)\n\n<audio src="review_clips/中文%20audio.m4a"></audio>\n\n[外部](https://example.org/info?q=1#part)\n')
        result = export_outputs(self.case, self.destination)
        self.assert_local_links_exist(result)
        copied = next(result.rglob('回听辅助索引.md')).read_text()
        self.assertIn('https://example.org/info?q=1#part', copied)

    def test_transcript_examples_inside_code_are_not_treated_as_dependencies(self):
        original = (self.work / '待核原文索引.md').read_text()
        example = '\n```text\n[原话中的格式例子](../../not-a-reading-file.txt)\n```\n\n`[另一个例子](../私人身份与配置.json)`\n'
        self.write(self.work / '待核原文索引.md', original + example)
        result = export_outputs(self.case, self.destination)
        copied = next(result.rglob('待核原文索引.md')).read_text()
        self.assertIn(example, copied)
        self.assertFalse(list(result.rglob('not-a-reading-file.txt')))

    def test_output_attachments_and_reference_links_keep_their_targets(self):
        image = self.output / '插图/合成图.png'
        image.parent.mkdir()
        image.write_bytes(b'synthetic image attachment')
        self.write(self.output / '01_公众号可发布稿.md', '# 正文\n\n![图](插图/合成图.png)\n')
        original = (self.work / '待核原文索引.md').read_text()
        self.write(self.work / '待核原文索引.md', original + '\n[相关正文][main]\n\n[main]: ../outputs/01_公众号可发布稿.md\n')
        result = export_outputs(self.case, self.destination)
        self.assert_local_links_exist(result)
        self.assertEqual((result / '插图/合成图.png').read_bytes(), image.read_bytes())
        copied = next(result.rglob('待核原文索引.md')).read_text()
        reference = re.search(r'^\[main\]: (.+)$', copied, re.M).group(1)
        self.assertTrue((result / '核对材料' / unquote(reference)).resolve().is_file())


if __name__ == '__main__':
    unittest.main()
