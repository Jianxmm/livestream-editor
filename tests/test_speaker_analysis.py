"""Synthetic, evidence-bound identity views; source files are never normalized in place."""
from pathlib import Path
import json
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from route_livestream import file_sha256
from speaker_analysis import build_speaker_analysis
from audit_solo_share import audit_case as audit_solo
from audit_draft import audit_edit_ledger, audit_retention
from speaker_pipeline import align_blocks
import test_audit_draft as dual_fixtures
from test_audit_solo_share import make_case as make_solo, add_routed_solo_bundle


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def bind_inputs(case, count='2'):
    source=case/'inputs/02_带时间码原稿.md'
    replay=case/'inputs/01_直播回放.mp4'
    route={'status':'PASS','mode':'dual' if count=='2' else 'solo',
           'target_skill':'references/dual.md' if count=='2' else 'references/solo.md',
           'acoustic_speaker_count':count,
           'source':{'path':source.relative_to(case).as_posix(),'sha256':file_sha256(source)},
           'replay':{'path':replay.relative_to(case).as_posix(),'sha256':file_sha256(replay),
                     'evidence':[f'合成记录{i}，不是真人回听' for i in range(5)]}}
    write_json(case/'_work/场次分流.json',route)
    if count=='2':
        cp=case/'_work/说话人校准.json';cal=json.loads(cp.read_text());cal['source_sha256']=file_sha256(replay);write_json(cp,cal)
        tp=case/'_work/说话人时间轴.json';timeline=json.loads(tp.read_text());timeline['provenance']={
            'replay_sha256':file_sha256(replay),'transcript_sha256':file_sha256(source),'calibration_sha256':file_sha256(cp)}
        write_json(tp,timeline)


class SpeakerAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='speaker identities ')
        self.root=Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()

    def dual(self):
        case=self.root/'dual';case.mkdir();return dual_fixtures.AuditDraftTest().make_case(case)

    def unlabelled(self, case):
        source=case/'inputs/02_带时间码原稿.md'
        source.write_text(re.sub(r'发言人[12]', '发言人未标注', source.read_text()),encoding='utf-8')
        cp=case/'_work/说话人校准.json';cal=json.loads(cp.read_text());cal['source_label_default_map']={'发言人未标注':None};write_json(cp,cal)
        tp=case/'_work/说话人时间轴.json';timeline=json.loads(tp.read_text())
        lines=['# 合成冲突记录','- 未解决冲突：0','| 冲突编号 | 级别 | 时间码 | 冲突类型 | 原转写标签 | 独立时间轴 | 上下文提示 | 处理结论 | 状态 |','| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
        for i,row in enumerate(timeline['source_blocks'],1):
            row.update(source_label='发言人未标注',status='真人已确认',conflict_id=f'C{i}')
            lines.append(f"| C{i} | P1 | {row['source_start']} | 未标注 | 发言人未标注 | {row['resolved_speaker_id']} | 合成测试 | 回放音频/合成夹具，非真人结论 | 已回听 |")
        write_json(tp,timeline)
        (case/'_work/说话人冲突清单.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        bind_inputs(case)
        return source

    def test_legacy_two_label_case_keeps_existing_parser(self):
        case=self.dual();source=(case/'inputs/02_带时间码原稿.md').read_text()
        self.assertIsNone(build_speaker_analysis(case,source))

    def test_unlabelled_blocks_use_verified_identities_without_altering_inputs(self):
        case=self.dual();source=self.unlabelled(case)
        before={str(p):p.read_bytes() for p in [source,case/'_work/说话人校准.json',case/'_work/说话人时间轴.json']}
        analysis=build_speaker_analysis(case,source.read_text())
        self.assertEqual(analysis.raw_block_count,6)
        self.assertEqual(analysis.source_turn_count,6)
        self.assertEqual(analysis.speaker_map,{'发言人1':'主持人','发言人2':'示例嘉宾'})
        strip=lambda text: re.sub(r'^发言人\S+\s+(?:\d{2}:)?\d{2}:\d{2}\s*$', '', text, flags=re.M)
        self.assertEqual(strip(source.read_text()),strip(analysis.source))
        self.assertTrue(all(Path(name).read_bytes()==data for name,data in before.items()))
        filtered=re.sub(r'(发言人1\s+00:30\s*\n).*?(?=^发言人2\s+00:40)',r'\1\n',analysis.source,flags=re.S|re.M)
        self.assertEqual(analysis.count_turns(source=filtered),4)

    def test_unknown_ledger_map_requires_analysis_and_remains_unknown(self):
        case=self.dual();source=self.unlabelled(case);analysis=build_speaker_analysis(case,source.read_text())
        ledger={'version':1,'source':'inputs/02_带时间码原稿.md','speaker_map':{'发言人未标注':None},'nontrivial_edits':[]}
        path=case/'_work/编辑决策台账.json'
        self.assertFalse(any(f.code=='EDIT_LEDGER_SPEAKERS' for f in audit_edit_ledger(json.dumps(ledger),source.read_text(),'','',path,speaker_analysis=analysis)))
        self.assertTrue(any(f.code=='EDIT_LEDGER_SPEAKERS' for f in audit_edit_ledger(json.dumps(ledger),source.read_text(),'','',path)))
        ledger['speaker_map']['发言人未标注']='主持人'
        self.assertTrue(any(f.code=='EDIT_LEDGER_SPEAKERS' for f in audit_edit_ledger(json.dumps(ledger),source.read_text(),'','',path,speaker_analysis=analysis)))
        self.assertEqual(json.loads(analysis.with_ledger(json.dumps(ledger)))['speaker_map'],analysis.speaker_map)

    def test_pending_block_cannot_become_a_confirmed_analysis_identity(self):
        case=self.dual();source=self.unlabelled(case)
        tp=case/'_work/说话人时间轴.json';timeline=json.loads(tp.read_text());timeline['source_blocks'][0]['status']='冲突待核';write_json(tp,timeline)
        with self.assertRaisesRegex(ValueError,'归属仍待核'):
            build_speaker_analysis(case,source.read_text())

    def test_current_binding_is_required_for_nonstandard_labels(self):
        for kind in ['source','calibration','timeline']:
            with self.subTest(kind=kind):
                # Each fixture lives separately so all three defects are independent.
                old_root=self.root;self.root=old_root/kind;self.root.mkdir()
                case=self.dual();source=self.unlabelled(case)
                if kind=='source':source.write_text(source.read_text()+'\n改动',encoding='utf-8')
                elif kind=='calibration':
                    cp=case/'_work/说话人校准.json';cp.write_text(cp.read_text()+'\n',encoding='utf-8')
                else:
                    tp=case/'_work/说话人时间轴.json';data=json.loads(tp.read_text());data['provenance']['replay_sha256']='stale';write_json(tp,data)
                with self.assertRaises(ValueError):build_speaker_analysis(case,source.read_text())
                self.root=old_root

    def test_bound_normal_labels_also_follow_confirmed_local_correction(self):
        case=self.dual();tp=case/'_work/说话人时间轴.json';timeline=json.loads(tp.read_text())
        row=timeline['source_blocks'][0];row.update(status='真人已确认',resolved_speaker_id='SPK_GUEST',conflict_id='C1');timeline['segments'][0]['speaker_id']='SPK_GUEST';write_json(tp,timeline)
        (case/'_work/说话人冲突清单.md').write_text('| C1 | P1 | 00:00 | 标签误分 | 发言人1 | SPK_GUEST | 合成 | 回放音频/合成夹具 | 已回听 |\n',encoding='utf-8')
        bind_inputs(case);source=(case/'inputs/02_带时间码原稿.md').read_text();analysis=build_speaker_analysis(case,source)
        self.assertTrue(analysis.source.startswith('发言人2'))
        self.assertEqual(analysis.raw_label_map,{'发言人1':'主持人','发言人2':'示例嘉宾'})
        self.assertEqual(analysis.source_turn_count,5)

    def test_subtitle_fragments_do_not_count_as_actual_dialogue_turns(self):
        case=self.dual();self.unlabelled(case)
        source=case/'inputs/02_带时间码原稿.md';payloads=[('主持人的问题与具体经验。' if i<50 else '嘉宾的回答和真实限制。')*20 for i in range(100)]
        stamps=[f'{i//60:02d}:{i%60:02d}' for i in range(100)]
        source.write_text('\n\n'.join(f'发言人未标注 {stamp}\n{text}' for stamp,text in zip(stamps,payloads)),encoding='utf-8')
        cp=case/'_work/说话人校准.json';cal=json.loads(cp.read_text())
        for ref,start,end in zip(cal['speakers'][1]['reference_segments'],['00:50','01:10'],['01:05','01:25']):ref.update(start=start,end=end)
        write_json(cp,cal)
        tp=case/'_work/说话人时间轴.json';timeline=json.loads(tp.read_text());timeline['segments']=[
            {'id':'A','start':'00:00','end':'00:50','raw_cluster':'A','speaker_id':'SPK_HOST','overlap':False},
            {'id':'B','start':'00:50','end':'01:40','raw_cluster':'B','speaker_id':'SPK_GUEST','overlap':False}]
        timeline['source_blocks']=[{'source_start':stamp,'source_label':'发言人未标注','timeline_segment_ids':['A' if i<50 else 'B'],
            'resolved_speaker_id':'SPK_HOST' if i<50 else 'SPK_GUEST','status':'真人已确认','conflict_id':f'C{i}','notes':'合成'} for i,stamp in enumerate(stamps)]
        write_json(tp,timeline)
        (case/'_work/说话人冲突清单.md').write_text('\n'.join(f'| C{i} | P1 | {stamp} | 未标注 | 发言人未标注 | 声音锚点 | 合成 | 回放音频/合成夹具 | 已回听 |' for i,stamp in enumerate(stamps))+'\n',encoding='utf-8')
        bind_inputs(case);analysis=build_speaker_analysis(case,source.read_text())
        self.assertEqual(analysis.raw_block_count,100);self.assertEqual(analysis.source_turn_count,2)
        self.assertEqual(analysis.count_turns(40),1)
        draft='**主持人：** '+''.join(payloads[:50])+'\n\n**示例嘉宾：** '+''.join(payloads[50:])
        self.assertTrue(any(f.code=='TOO_FEW_DIALOGUE_TURNS' for f in audit_retention(analysis.source,draft,case/'draft')))
        self.assertFalse(any(f.code=='TOO_FEW_DIALOGUE_TURNS' for f in audit_retention(analysis.source,draft,case/'draft',source_turn_count=analysis.source_turn_count)))

    def test_routed_solo_accepts_platform_mislabels_only_with_current_one_person_binding(self):
        case=make_solo(self.root);add_routed_solo_bundle(case)
        source=case/'inputs/02_带时间码原稿.md';source.write_text(source.read_text().replace('发言人1   20:00','发言人2   20:00'),encoding='utf-8')
        draft=case/'outputs/01_公众号可发布稿.md';draft.write_text(draft.read_text().replace('**示例主持：** ',''),encoding='utf-8')
        bind_inputs(case,'1');before=source.read_bytes()
        self.assertFalse(any('NOT_EXACTLY_ONE_SPEAKER' in e for e in audit_solo(case).errors))
        self.assertEqual(source.read_bytes(),before)
        source.write_text(source.read_text()+'\n另一次改动',encoding='utf-8')
        self.assertTrue(any('NOT_EXACTLY_ONE_SPEAKER' in e for e in audit_solo(case).errors))
        (case/'_work/场次分流.json').unlink()
        self.assertTrue(any('NOT_EXACTLY_ONE_SPEAKER' in e for e in audit_solo(case).errors))

    def test_unknown_caption_labels_do_not_create_false_human_conflicts(self):
        segment={'id':'A','start_seconds':0,'end_seconds':20,'speaker_id':'KNOWN_A','uncertain':False,'overlap':False}
        blocks,conflicts=align_blocks('发言人未标注 00:00\n原话。\n\n发言人未标注 00:05\n继续原话。',[segment],{'发言人未标注':None},20)
        self.assertEqual([block['status'] for block in blocks],['声学归属','声学归属'])
        self.assertEqual(conflicts,[])
        self.assertTrue(all(block['conflict_id'] is None for block in blocks))
        for field,value in [('uncertain',True),('overlap',True),('end_seconds',2)]:
            with self.subTest(field=field):
                changed=dict(segment);changed[field]=value
                blocks,conflicts=align_blocks('发言人未标注 00:00\n原话。',[changed],{'发言人未标注':None},20)
                self.assertNotEqual(blocks[0]['status'],'声学归属')
                self.assertEqual(len(conflicts),1)

    def acoustic_case(self):
        case=self.dual();source=self.unlabelled(case)
        tp=case/'_work/说话人时间轴.json';timeline=json.loads(tp.read_text())
        for row in timeline['source_blocks']:row.update(status='声学归属',conflict_id=None)
        for row in timeline['segments']:row['uncertain']=False
        write_json(tp,timeline)
        (case/'_work/说话人冲突清单.md').write_text('# 没有虚构的人工冲突\n',encoding='utf-8')
        return case,source

    def test_clean_acoustic_identity_is_usable_but_never_called_human_confirmation(self):
        case,source=self.acoustic_case();analysis=build_speaker_analysis(case,source.read_text())
        self.assertEqual(analysis.source_turn_count,6)
        timeline=json.loads((case/'_work/说话人时间轴.json').read_text())
        self.assertEqual({row['status'] for row in timeline['source_blocks']},{'声学归属'})
        self.assertTrue(all(row['conflict_id'] is None for row in timeline['source_blocks']))

    def test_uncertain_acoustic_evidence_cannot_silently_supply_an_identity(self):
        case,source=self.acoustic_case();tp=case/'_work/说话人时间轴.json'
        original=json.loads(tp.read_text())
        for field,value in [('uncertain',True),('overlap',True),('end','00:02')]:
            with self.subTest(field=field):
                data=json.loads(json.dumps(original));data['segments'][0][field]=value;write_json(tp,data)
                with self.assertRaisesRegex(ValueError,'不确定声音'):
                    build_speaker_analysis(case,source.read_text())


if __name__=='__main__':unittest.main()
