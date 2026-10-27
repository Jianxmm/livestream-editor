"""Distribution and provenance behavior, using only disposable synthetic fixtures."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from workspace import init_case, templates, inspect_case, export_outputs, probe
from route_livestream import build_route, write_route, file_sha256
from route_fixture import observations_for
from build_source_review_index import build_source_review_index
from audit_package import shared_errors, matching_review_rows
from speaker_pipeline import validate_calibration, align_blocks, validate_input_binding
from build_release import build, release_files
from freeze_input_bundle import inspect_input_bundle
from track_human_review import freeze_baseline, summarize_human_review
from audit_draft import audit_human_review_metrics
from register_validation_campaign import register_campaign
from build_reading_index import build_reading_index

class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='独立安装 test ')
        self.root=Path(self.temp.name)
        self.media=self.root/'素材音频.WAV';self.media.write_bytes(b'fixture media')
        self.source=self.root/'原稿.md';self.source.write_text('\ufeff发言人1   00:00\n这是测试。\n\n发言人2   00:30\n回应。\n',encoding='utf-8')
        self.case=init_case(self.root/'案例',self.media,self.source)
        self.evidence=[f'{i*10:02d}:00 合成测试证据 {i}' for i in range(5)]
    def tearDown(self):self.temp.cleanup()
    def route(self,count='1'):
        route=build_route(self.case,acoustic_speakers=count,evidence=self.evidence,observations=observations_for(self.case))
        write_route(self.case,route);return route
    def test_init_preserves_bytes_extension_and_bom(self):
        self.assertEqual(self.source.read_bytes(),(self.case/'inputs/02_带时间码原稿.md').read_bytes())
        self.assertEqual(self.media.read_bytes(),(self.case/'inputs/01_直播回放.wav').read_bytes())
        with self.assertRaises(FileExistsError):init_case(self.case,self.media,self.source)
    def test_invalid_input_leaves_no_case(self):
        self.source.write_text('无时间码的摘要')
        with self.assertRaises(ValueError):init_case(self.root/'bad',self.media,self.source)
        self.assertFalse((self.root/'bad').exists())
    def test_changed_original_copy_is_detected(self):
        with patch('workspace.duration',return_value=90):self.assertFalse(inspect_case(self.case)['errors'])
        (self.case/'inputs/02_带时间码原稿.md').write_text('发言人1 00:00\n修改')
        with patch('workspace.duration',return_value=90):self.assertTrue(inspect_case(self.case)['errors'])
    def test_frozen_bundle_accepts_media_extension_and_uses_portable_paths(self):
        frozen=inspect_input_bundle(self.case)
        paths={item['path'] for item in frozen['files']}
        self.assertIn('inputs/01_直播回放.wav',paths)
        self.assertIn('inputs/02_带时间码原稿.md',paths)
        self.assertTrue(all('\\' not in path and '/' in path for path in paths))
    def test_crlf_bom_review_hashes_match_exact_input_bytes(self):
        draft=self.case/'outputs/01_公众号可发布稿.md'
        draft.write_bytes(('\ufeff# 技术测试\r\n正文。\r\n').encode('utf-8'))
        freeze_baseline(self.case)
        metrics=summarize_human_review(self.case,'测试编辑','2026-10-22',12)
        self.assertEqual(metrics['source_sha256'],file_sha256(self.case/'inputs/02_带时间码原稿.md'))
        self.assertEqual(metrics['final_draft_sha256'],file_sha256(draft))
        report='- 终审人：测试编辑\n- 终审日期：2026-10-22\n- 人工终审耗时：12分钟\n'
        self.assertEqual(audit_human_review_metrics(self.case,report),[])
    def test_campaign_binds_raw_crlf_files_but_rejects_same_text(self):
        cases=[]
        for i in range(3):
            case=self.root/f'campaign-{i}';(case/'inputs').mkdir(parents=True)
            (case/'inputs/01_直播回放.wav').write_bytes(b'synthetic media')
            (case/'inputs/02_带时间码原稿.md').write_bytes((f'\ufeff发言人1 00:00\r\n独立样例{i}\r\n').encode('utf-8'))
            cases.append(case)
        manifest=register_campaign(cases,self.root/'campaign-result','技术测试')
        for case,item in zip(cases,manifest['samples']):
            self.assertEqual(item['source_sha256'],file_sha256(case/'inputs/02_带时间码原稿.md'))
        text=(cases[0]/'inputs/02_带时间码原稿.md').read_text(encoding='utf-8-sig')
        (cases[1]/'inputs/02_带时间码原稿.md').write_bytes(text.encode('utf-8'))
        with self.assertRaises(ValueError):register_campaign(cases,self.root/'duplicates','重复测试')
    def test_duplicate_route_evidence_is_not_five_checks(self):
        with self.assertRaises(ValueError):build_route(self.case,acoustic_speakers='1',evidence=['same']*5)
    def test_templates_bind_extension_and_preserve_edits(self):
        self.route('2');templates(self.case,'dual')
        cp=self.case/'_work/说话人校准.json';c=json.loads(cp.read_text())
        self.assertEqual(c['source_audio'],'inputs/01_直播回放.wav')
        self.assertEqual(c['source_sha256'],file_sha256(self.media))
        target=self.case/'outputs/01_公众号可发布稿.md';target.write_text('人工修改')
        templates(self.case,'dual');self.assertEqual(target.read_text(),'人工修改')
    def test_short_probe_reuses_audio_and_shows_each_window_once(self):
        def make_audio(source, output, *args, **kwargs):output.write_bytes(b'synthetic clip')
        with patch('workspace.duration',return_value=27.74), patch('workspace.audio_clip',side_effect=make_audio) as convert:
            folder=probe(self.case)
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(convert.call_count,3)
        self.assertEqual(manifest['unique_clip_count'],3)
        self.assertEqual(len(manifest['clips']),5)
        sheet=(folder/'回听.md').read_text(encoding='utf-8')
        self.assertEqual(sheet.count('](03.m4a)'),1)
        self.assertIn('重复片段',sheet)
    def test_shared_gate_rejects_stale_index_and_route(self):
        self.route();issues=self.case/'outputs/02_待核问题清单.md'
        issues.write_text('# 待核清单\n无疑点。')
        build_source_review_index(self.case)
        self.assertEqual(shared_errors(self.case),('solo',[]))
        issues.write_text('新增待核问题')
        self.assertTrue(any('索引' in e for e in shared_errors(self.case)[1]))
        (self.case/'inputs/01_直播回放.wav').write_bytes(b'changed')
        self.assertTrue(any('输入已变化' in e for e in shared_errors(self.case)[1]))
    def test_shared_audio_has_separate_issue_conclusions(self):
        sheet='| 1 | 00:01 | fact A | `same.m4a` | confirmed | kept | reviewer 2026-01-01 |\n| 2 | 00:01 | fact B | `same.m4a` | pending | pending | pending |'
        rows=matching_review_rows(sheet,{'filename':'same.m4a','priority':'P1','issue_index':1})
        self.assertEqual(len(rows),1);self.assertIn('fact A',rows[0])
        rows=matching_review_rows(sheet,{'filename':'same.m4a','priority':'P1','issue_index':2})
        self.assertEqual(len(rows),1);self.assertIn('pending',rows[0])
    def make_outputs(self):
        for name in ['01_公众号可发布稿.md','02_待核问题清单.md','03_编辑质检报告.md']:
            (self.case/'outputs'/name).write_text('技术测试正文，不是实际整理结果。',encoding='utf-8')
    def test_reading_index_links_outputs_without_rewriting_them(self):
        self.route('2');self.make_outputs()
        before={p.name:p.read_bytes() for p in (self.case/'outputs').iterdir()}
        index=build_reading_index(self.case)
        self.assertIn('双人访谈整理稿',index.read_text(encoding='utf-8'))
        self.assertEqual(index,build_reading_index(self.case))
        for name,content in before.items():self.assertEqual((index.parent/name).read_bytes(),content)
    def test_solo_reading_index_requires_and_links_full_audio(self):
        self.route();self.make_outputs()
        with self.assertRaises(FileNotFoundError):build_reading_index(self.case)
        (self.case/'outputs/04_整场音频.m4a').write_bytes(b'synthetic audio fixture')
        index=build_reading_index(self.case)
        self.assertIn('[整场音频](04_整场音频.m4a)',index.read_text(encoding='utf-8'))
    def test_reading_index_does_not_overwrite_human_edits(self):
        self.route('2');self.make_outputs();index=build_reading_index(self.case)
        index.write_text('使用者自己写的阅读提示',encoding='utf-8')
        with self.assertRaises(FileExistsError):build_reading_index(self.case)
        self.assertEqual(index.read_text(encoding='utf-8'),'使用者自己写的阅读提示')
    def test_reading_index_rejects_stale_source(self):
        self.route('2');self.make_outputs()
        (self.case/'inputs/02_带时间码原稿.md').write_text('发言人1 00:00\n已换稿',encoding='utf-8')
        with self.assertRaises(ValueError):build_reading_index(self.case)
        self.assertFalse((self.case/'outputs/00_从这里开始.md').exists())
    def test_model_requires_current_confirmed_route(self):
        self.route('2');c={'source_sha256':file_sha256(self.media)}
        validate_input_binding(self.case,c)
        c['source_sha256']='old'
        with self.assertRaises(ValueError):validate_input_binding(self.case,c)
    def test_export_does_not_replace_human_copy(self):
        for name in ['01_公众号可发布稿.md','02_待核问题清单.md','03_编辑质检报告.md']:
            (self.case/'outputs'/name).write_text('自动草稿')
        target=export_outputs(self.case,self.root/'阅读副本')
        (target/'01_公众号可发布稿.md').write_text('人工稿')
        with self.assertRaises(FileExistsError):export_outputs(self.case,target)
        self.assertEqual((target/'01_公众号可发布稿.md').read_text(),'人工稿')
    def test_reproducible_package_and_no_private_tree(self):
        root=self.root/'skill';root.mkdir()
        (root/'SKILL.md').write_text('synthetic skill');(root/'VERSION').write_text('0.0.test')
        (root/'release-files.json').write_text(json.dumps(['SKILL.md','VERSION','release-files.json']))
        (root/'cases').mkdir();(root/'cases/private.md').write_text('never ship')
        a=build(self.root/'a.zip',root);b=build(self.root/'b.zip',root)
        self.assertEqual(a['sha256'],b['sha256']);self.assertEqual(a['files'],4)
        with self.assertRaises(FileExistsError):build(self.root/'a.zip',root)
    def test_whitelist_rejects_credentials_even_when_listed(self):
        root=self.root/'private-skill';root.mkdir()
        for name in ['.env','.env.local','id_rsa','certificate.pem','hosts.yml']:
            (root/name).write_text('synthetic secret fixture')
            (root/'release-files.json').write_text(json.dumps([name]))
            with self.assertRaises(ValueError):release_files(root)
    def test_whitelist_rejects_private_files_and_symlinks(self):
        root=self.root/'skill';root.mkdir()
        (root/'release-files.json').write_text(json.dumps(['cases/private.md']))
        with self.assertRaises(ValueError):release_files(root)
        try:(root/'SKILL.md').symlink_to(self.source)
        except OSError:self.skipTest('本系统未授权创建测试符号链接')
        (root/'release-files.json').write_text(json.dumps(['SKILL.md']))
        with self.assertRaises(ValueError):release_files(root)

class AcousticTests(unittest.TestCase):
    def calibration(self):
        return {'status':'已确认','source_label_default_map':{'发言人1':'A','发言人2':'B'},'speakers':[
            {'speaker_id':sid,'name':name,'reference_segments':[{'start':start,'end':end,'identity_basis':'测试夹具由已知两种合成声音生成','review_status':'已确认','overlap':False} for start,end in refs]}
            for sid,name,refs in [('A','测试甲',[('00:00','00:15'),('00:15','00:30')]),('B','测试乙',[('00:30','00:45'),('00:45','01:00')])]]}
    def test_calibration_requires_independent_anchors(self):
        c=self.calibration();validate_calibration(c,90)
        c['speakers'][0]['reference_segments'][1]=copy.deepcopy(c['speakers'][0]['reference_segments'][0])
        with self.assertRaises(ValueError):validate_calibration(c,90)
    def test_labels_do_not_override_mixed_audio(self):
        segments=[{'id':'1','start_seconds':0,'end_seconds':15,'speaker_id':'A'}, {'id':'2','start_seconds':15,'end_seconds':30,'speaker_id':'B'}]
        blocks,conflicts=align_blocks('发言人1 00:00\n混合发言\n',segments,{'发言人1':'A'},30)
        self.assertEqual(blocks[0]['status'],'混合说话人');self.assertIsNone(blocks[0]['resolved_speaker_id']);self.assertEqual(len(conflicts),1)
    def test_missing_audio_never_uses_transcript_identity(self):
        blocks,conflicts=align_blocks('发言人1 00:00\n文本\n',[],{'发言人1':'A'},30)
        self.assertEqual(blocks[0]['status'],'无法映射');self.assertIsNone(blocks[0]['resolved_speaker_id'])

if __name__=='__main__':unittest.main()
