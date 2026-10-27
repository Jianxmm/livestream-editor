"""Ordered transcript turns preserve seconds and identity uncertainty."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from workspace import init_case,inspect_case,input_manifest_errors

SOURCE='发言人1   00:01\n开场。\n\n发言人1   00:10\n对。\n\n发言人2   00:10\n继续提问。\n\n发言人2   00:20\n结尾。\n'


class OrderedTurnTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.video=self.root/'source.wav';self.video.write_bytes(b'fixture-not-real-media')
        self.source=self.root/'source.md';self.source.write_text(SOURCE)
    def tearDown(self):self.temp.cleanup()
    def create(self):
        return init_case(self.root/'case',self.video,self.source,transcript_contract='ordered-turns')
    def test_explicit_contract_preserves_raw_bytes_and_distinct_ids(self):
        raw=b'\xef\xbb\xbf'+SOURCE.replace('\n','\r\n').encode();self.source.write_bytes(raw)
        case=self.create();self.assertEqual((case/'inputs/02_带时间码原稿.md').read_bytes(),raw)
        index=json.loads((case/'_work/原稿逐块索引.json').read_text())
        self.assertEqual(len(index['blocks']),4)
        self.assertEqual(len({b['id'] for b in index['blocks']}),4)
        self.assertEqual([b['timecode'] for b in index['blocks']][1:3],['00:10','00:10'])
        with patch('workspace.duration',return_value=30):
            self.assertFalse(inspect_case(case)['errors']);self.assertTrue(any('同秒' in x for x in inspect_case(case)['warnings']))
    def test_default_import_still_rejects_equal_times(self):
        with self.assertRaisesRegex(ValueError,'递增'):init_case(self.root/'case',self.video,self.source)
    def test_backwards_still_rejected_atomically(self):
        self.source.write_text(SOURCE.replace('00:20','00:09'))
        with self.assertRaises(ValueError):self.create()
        self.assertFalse((self.root/'case').exists())
    def test_subtitle_contract_is_not_relaxed(self):
        self.source=self.root/'source.srt';self.source.write_text('1\n00:00:00,000 --> 00:00:01,000\n原话\n')
        with self.assertRaises(ValueError):self.create()
    def test_missing_or_tampered_block_index_rejected(self):
        case=self.create();p=case/'_work/原稿逐块索引.json';raw=p.read_bytes();p.unlink()
        self.assertTrue(input_manifest_errors(case));p.write_bytes(raw)
        value=json.loads(raw);value['blocks'][2]['id']=value['blocks'][1]['id'];p.write_text(json.dumps(value))
        self.assertTrue(input_manifest_errors(case))
    def test_source_change_cannot_reuse_index(self):
        case=self.create();(case/'inputs/02_带时间码原稿.md').write_text(SOURCE+'改动')
        self.assertTrue(input_manifest_errors(case))
    def test_same_second_alignment_stays_unresolved_even_for_one_clean_voice(self):
        from speaker_pipeline import align_blocks
        segments=[{'id':'S1','start_seconds':0,'end_seconds':30,'speaker_id':'A','uncertain':False,'overlap':False}]
        with self.assertRaises(ValueError):align_blocks(SOURCE,segments,{'发言人1':'A','发言人2':'A'},30)
        blocks,conflicts=align_blocks(SOURCE,segments,{'发言人1':'A','发言人2':'A'},30,allow_same_second=True)
        self.assertEqual(len(blocks),4);self.assertEqual(len({b['source_block_id'] for b in blocks}),4)
        for block in blocks[1:3]:
            self.assertEqual(block['status'],'无法映射');self.assertIsNone(block['resolved_speaker_id'])
            self.assertTrue(block['timing_ambiguous']);self.assertEqual(block['source_start'],'00:10')
        self.assertEqual(len({b['conflict_id'] for b in blocks[1:3]}),2)
    def test_zero_length_at_media_end_is_not_invented(self):
        from speaker_pipeline import align_blocks
        with self.assertRaises(ValueError):align_blocks(SOURCE,[],{},20,allow_same_second=True)
    def test_review_index_keeps_both_original_blocks_and_unique_anchors(self):
        from build_source_review_index import build_source_review_index
        case=self.create();(case/'outputs/02_待核问题清单.md').write_text('| P1 | 00:10 | 同秒换人 | 归属待核 | 核对原回放 | 待确认 |\n')
        build_source_review_index(case);value=(case/'_work/待核原文索引.md').read_text()
        self.assertIn('对。',value);self.assertIn('继续提问。',value)
        index=json.loads((case/'_work/原稿逐块索引.json').read_text())
        for row in index['blocks'][1:3]:self.assertIn('id="source-'+row['id']+'"',value)
    def test_same_label_repeated_at_same_second_cannot_collapse(self):
        from source_turns import blocks_from_text,timeline_identity_errors
        source=SOURCE.replace('发言人2   00:10','发言人1   00:10')
        rows=blocks_from_text(source)
        self.assertNotEqual(rows[1]['id'],rows[2]['id'])
        timeline=[{'source_block_id':r['id'],'source_start':r['timecode'],'source_label':r['label'],
                   'timing_ambiguous':r['same_second'],'status':'无法映射','conflict_id':str(i)} for i,r in enumerate(rows)]
        self.assertFalse(timeline_identity_errors(source,timeline))
        timeline[2]['source_block_id']=timeline[1]['source_block_id']
        self.assertTrue(timeline_identity_errors(source,timeline))
    def test_tied_blocks_cannot_be_auto_confirmed_or_reordered(self):
        from source_turns import blocks_from_text,timeline_identity_errors
        rows=blocks_from_text(SOURCE)
        timeline=[{'source_block_id':r['id'],'source_start':r['timecode'],'source_label':r['label'],
                   'timing_ambiguous':r['same_second'],'status':'无法映射','conflict_id':str(i)} for i,r in enumerate(rows)]
        timeline[1]['status']='一致';self.assertTrue(timeline_identity_errors(SOURCE,timeline))
        timeline[1]['status']='无法映射';timeline[1],timeline[2]=timeline[2],timeline[1]
        self.assertTrue(timeline_identity_errors(SOURCE,timeline))
    def test_real_preflight_rejects_missing_turn_ids(self):
        import test_audit_draft as fixture
        from audit_draft import audit_speaker_preflight
        case=self.create();timeline=json.loads(json.dumps(fixture.SPEAKER_TIMELINE))
        timeline['source_blocks']=[{'source_start':'00:10','source_label':'发言人1','status':'一致',
                                   'timeline_segment_ids':['S001'],'resolved_speaker_id':'SPK_HOST'}]*4
        findings=audit_speaker_preflight(SOURCE,'',json.dumps(fixture.SPEAKER_CALIBRATION),json.dumps(timeline),
                                        fixture.SPEAKER_CONFLICTS,case/'_work/说话人校准.json',case/'_work/说话人时间轴.json',case/'_work/说话人冲突清单.md')
        self.assertIn('SPEAKER_SOURCE_BLOCK_IDENTITY',{f.code for f in findings if f.level=='ERROR'})
    def test_two_tied_blocks_cannot_share_one_confirmation(self):
        from source_turns import blocks_from_text,timeline_identity_errors
        rows=blocks_from_text(SOURCE)
        timeline=[{'source_block_id':r['id'],'source_start':r['timecode'],'source_label':r['label'],
                   'timing_ambiguous':r['same_second'],'status':'真人已确认','conflict_id':'shared'} for r in rows]
        self.assertTrue(timeline_identity_errors(SOURCE,timeline))


if __name__=='__main__':unittest.main()
