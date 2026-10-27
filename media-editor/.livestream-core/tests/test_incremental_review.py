"""Exercise a human closing issues and continuing the same review package."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from extract_review_clips import extract_review_package, format_timecode
from audit_package import issue_handoff_errors


class IncrementalReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='incremental review ')
        self.case = Path(self.temp.name)
        for name in ['inputs', 'outputs', '_work']:
            (self.case / name).mkdir()
        (self.case / 'inputs/01_直播回放.wav').write_bytes(b'synthetic media, not a real review')
        (self.case / 'inputs/02_带时间码原稿.md').write_text('\n\n'.join(
            f'发言人1   {format_timecode(t)}\n合成测试原文，时间{t}秒。' for t in range(0, 3901, 180)), encoding='utf-8')
        (self.case / 'outputs/01_公众号可发布稿.md').write_text('# 合成正文\n已有正文。', encoding='utf-8')
        self.issues = self.case / 'outputs/02_待核问题清单.md'
        self.sheet = self.case / '_work/回听终审表.md'
        self.folder = self.case / '_work/review_clips'
        self.old = ('00:30', '人名', '原问题中的姓名')
        self.new = ('01:30', '数字', '新增问题中的数量')
        self.write_issues([(self.old, '待确认')])
        self.audio_calls = []
        def fake_audio(source, target, start, duration):
            self.audio_calls.append((start, duration))
            target.write_bytes(f'synthetic clip at {start} duration {duration}'.encode())
        self.patches = [patch('extract_review_clips.duration', return_value=4100),
                        patch('extract_review_clips.audio_clip', side_effect=fake_audio)]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def write_issues(self, rows):
        self.issues.write_text('| 级别 | 时间码 | 问题类型 | 原文/成稿 | 处理建议 | 状态 |\n'
                              '| --- | --- | --- | --- | --- | --- |\n' + ''.join(
            f'| P1 | {item[0]} | {item[1]} | {item[2]} | 合成核对依据 | {status} |\n' for item, status in rows), encoding='utf-8')

    def manifest(self):
        return json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))

    def finish_old_rows(self):
        text = self.sheet.read_text(encoding='utf-8')
        text = text.replace('| 待填 | 待定 | 待填 |', '| 合成核验原话 | 保留正文写法 | 测试编辑 / 2026-10-23 |')
        text = text.replace('| 待填 | 待填 | 待填 |', '| 合成抽听已核 | 否 | 测试编辑 / 2026-10-23 |')
        self.sheet.write_text(text, encoding='utf-8')

    def test_close_then_add_preserves_old_clip_signature_and_sample_evidence(self):
        extract_review_package(self.case)
        first = self.manifest()
        self.assertEqual(len(first['samples']), 5)
        self.finish_old_rows()
        old_line = next(line for line in self.sheet.read_text().splitlines() if line.startswith('| 1 |'))
        self.audio_calls.clear()
        self.write_issues([(self.new, '待确认'), (self.old, '已回听')])
        extract_review_package(self.case)
        current = self.manifest()
        old = next(item for item in current['clips'] if item['issue_text'] == self.old[2])
        added = next(item for item in current['clips'] if item['issue_text'] == self.new[2])
        self.assertEqual(old['issue_index'], 1)
        self.assertEqual(added['issue_index'], 2)
        self.assertEqual(old['audio_sha256'], first['clips'][0]['audio_sha256'])
        self.assertEqual(first['samples'], current['samples'])
        self.assertIn(old_line, self.sheet.read_text())
        self.assertEqual(len(self.audio_calls), 1)
        self.assertTrue(any('明确关闭方式' in e for e in issue_handoff_errors(self.case, current)))
        self.assertFalse(any('缺少' in e for e in issue_handoff_errors(self.case, current)))
        self.finish_old_rows()
        self.write_issues([(self.new, '已回听'), (self.old, '已回听')])
        extract_review_package(self.case)
        self.assertEqual(issue_handoff_errors(self.case, self.manifest()), [])

    def test_status_only_and_reordering_are_idempotent(self):
        self.write_issues([(self.old, '待确认'), (self.new, '待确认')])
        extract_review_package(self.case); self.finish_old_rows()
        first = self.manifest(); before = self.sheet.read_bytes(); self.audio_calls.clear()
        self.write_issues([(self.new, '已回听'), (self.old, '已回听')])
        extract_review_package(self.case)
        self.assertEqual(self.sheet.read_bytes(), before)
        self.assertEqual(self.audio_calls, [])
        self.assertEqual(first['clips'], self.manifest()['clips'])

    def test_changed_fact_gets_fresh_identity_and_pending_row(self):
        extract_review_package(self.case); self.finish_old_rows()
        old = self.manifest()['clips'][0]
        changed = (self.old[0], self.old[1], '另一个人的姓名')
        self.write_issues([(changed, '待确认')]); extract_review_package(self.case)
        current = self.manifest()['clips'][0]
        self.assertNotEqual(current['issue_id'], old['issue_id'])
        self.assertNotEqual(current['issue_index'], old['issue_index'])
        line = next(line for line in self.sheet.read_text().splitlines() if '另一个人的姓名' in line)
        self.assertIn('| 待填 | 待定 | 待填 |', line)
        self.assertNotIn('测试编辑', line)
        history = list((self.folder / 'history').rglob('回听终审表.md'))
        self.assertTrue(any('原问题中的姓名' in p.read_text() and '测试编辑' in p.read_text() for p in history))

    def test_changed_time_cannot_keep_old_heard_claim(self):
        extract_review_package(self.case); self.finish_old_rows()
        before = self.sheet.read_bytes()
        changed = ('00:40', self.old[1], self.old[2])
        self.write_issues([(changed, '已回听')])
        with self.assertRaisesRegex(ValueError, '已回听'):
            extract_review_package(self.case)
        self.assertEqual(self.sheet.read_bytes(), before)

    def test_removed_issue_is_archived_and_manual_files_survive(self):
        extract_review_package(self.case); self.finish_old_rows()
        old_file = self.manifest()['clips'][0]['filename']
        manual = self.folder / '人工备注.md'; manual.write_text('保留人工文件', encoding='utf-8')
        self.write_issues([]); extract_review_package(self.case)
        self.assertEqual(self.manifest()['clips'], [])
        self.assertEqual(manual.read_text(), '保留人工文件')
        self.assertTrue(list((self.folder / 'history').rglob(old_file)))
        self.assertTrue(any(not r['active'] for r in self.manifest()['issue_registry']))

    def test_changed_source_blocks_even_explicit_sheet_refresh(self):
        extract_review_package(self.case)
        before = self.sheet.read_bytes()
        with (self.case / 'inputs/01_直播回放.wav').open('ab') as f: f.write(b'changed')
        with self.assertRaisesRegex(ValueError, '回放或原稿变化'):
            extract_review_package(self.case, refresh_review_sheet=True)
        self.assertEqual(self.sheet.read_bytes(), before)

    def test_changed_human_question_or_corrupt_audio_cannot_be_silently_replaced(self):
        extract_review_package(self.case); self.finish_old_rows()
        original = self.sheet.read_text()
        self.sheet.write_text(original.replace('原问题中的姓名', '人工换成了另一问题'))
        with self.assertRaisesRegex(ValueError, '终审表'):
            extract_review_package(self.case)
        self.sheet.write_text(original)
        clip = self.folder / self.manifest()['clips'][0]['filename']; clip.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, '音频'):
            extract_review_package(self.case)
        self.assertEqual(self.sheet.read_text(), original)

    def test_legacy_manifest_without_stable_ids_is_migrated_without_relistening(self):
        extract_review_package(self.case); self.finish_old_rows()
        manifest = self.manifest()
        manifest.pop('issue_registry')
        for item in manifest['clips']: item.pop('issue_id')
        (self.folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        before = self.sheet.read_text(); self.audio_calls.clear()
        self.write_issues([(self.old, '已回听'), (self.new, '待确认')])
        extract_review_package(self.case)
        self.assertIn(next(line for line in before.splitlines() if line.startswith('| 1 |')), self.sheet.read_text())
        self.assertEqual(self.manifest()['clips'][0]['issue_index'], 1)
        self.assertEqual(len(self.audio_calls), 1)

    def test_explicit_refresh_cannot_erase_human_conclusions(self):
        extract_review_package(self.case); self.finish_old_rows()
        before = self.sheet.read_bytes()
        with self.assertRaisesRegex(ValueError, '人工'):
            extract_review_package(self.case, refresh_review_sheet=True)
        self.assertEqual(self.sheet.read_bytes(), before)

    def test_new_issue_overlapping_random_sample_replaces_only_that_sample(self):
        extract_review_package(self.case); self.finish_old_rows()
        first = self.manifest()
        replaced = first['samples'][0]
        question = (replaced['timecode'], '新事实', '与旧随机片段同位置的疑问')
        self.write_issues([(self.old, '已回听'), (question, '待确认')])
        self.audio_calls.clear(); extract_review_package(self.case)
        current = self.manifest()
        self.assertEqual(len(current['samples']), 5)
        self.assertNotIn(replaced['filename'], {s['filename'] for s in current['samples']})
        keep = {s['filename']: s for s in first['samples'][1:]}
        self.assertEqual(keep, {s['filename']: s for s in current['samples'] if s['filename'] in keep})
        for sample in current['samples']:
            if sample['filename'] not in keep:
                line = next(line for line in self.sheet.read_text().splitlines() if f"`{sample['filename']}`" in line)
                self.assertIn('| 待填 | 待填 | 待填 |', line)
        self.assertTrue(list((self.folder / 'history').rglob(replaced['filename'])))
        self.assertEqual(len(self.audio_calls), 2)

    def test_failed_new_encoding_keeps_current_files_and_human_sheet(self):
        extract_review_package(self.case); self.finish_old_rows()
        old_files = {p.relative_to(self.folder).as_posix(): p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        old_sheet = self.sheet.read_bytes()
        self.write_issues([(self.old, '已回听'), (self.new, '待确认')])
        with patch('extract_review_clips.audio_clip', side_effect=RuntimeError('合成编码失败')):
            with self.assertRaisesRegex(RuntimeError, '编码失败'):
                extract_review_package(self.case)
        self.assertEqual(old_sheet, self.sheet.read_bytes())
        self.assertEqual(old_files, {p.relative_to(self.folder).as_posix(): p.read_bytes() for p in self.folder.rglob('*') if p.is_file()})

    def test_missing_old_sheet_is_not_recreated_as_if_conclusions_never_existed(self):
        extract_review_package(self.case)
        self.sheet.unlink()
        before = (self.folder / 'manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '终审表缺失'):
            extract_review_package(self.case)
        self.assertEqual(before, (self.folder / 'manifest.json').read_bytes())
        self.assertFalse(self.sheet.exists())

    def test_custom_clip_parameters_apply_to_new_issues_without_rewriting_old_audio(self):
        extract_review_package(self.case, padding=2, max_duration=20)
        old = self.manifest()['clips']
        self.assertTrue(len(old) > 1 and all(c['duration'] <= 20 for c in old))
        self.finish_old_rows(); self.audio_calls.clear()
        self.write_issues([(self.old, '已回听'), (self.new, '待确认')])
        extract_review_package(self.case, padding=5, max_duration=60)
        current = self.manifest()['clips']
        self.assertEqual(old, [c for c in current if c['issue_text'] == self.old[2]])
        added = [c for c in current if c['issue_text'] == self.new[2]]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]['start'], 85)
        self.assertEqual(len(self.audio_calls), 1)

    def test_dry_run_needs_no_media_decoder_and_writes_nothing(self):
        with patch('extract_review_clips.duration', side_effect=AssertionError('不能解码')):
            planned = extract_review_package(self.case, dry_run=True)
        self.assertTrue(planned['planned'])
        self.assertFalse(self.sheet.exists())
        self.assertFalse(self.folder.exists())

    def test_new_clip_name_collision_cannot_overwrite_an_unmanaged_file(self):
        extract_review_package(self.case); self.finish_old_rows()
        occupied = self.folder / 'P1-02-01-00h01m22s.m4a'
        occupied.write_bytes(b'user managed audio')
        before = self.sheet.read_bytes()
        self.write_issues([(self.old, '已回听'), (self.new, '待确认')])
        with self.assertRaisesRegex(ValueError, '其他文件占用'):
            extract_review_package(self.case)
        self.assertEqual(occupied.read_bytes(), b'user managed audio')
        self.assertEqual(self.sheet.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
