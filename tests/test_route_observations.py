"""Five real positions must be recorded; five different strings are insufficient."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from route_livestream import build_route,route_position_errors,file_sha256
from route_fixture import observations_for


class RouteObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.case=Path(self.temp.name)
        (self.case/'inputs').mkdir()
        (self.case/'inputs/01_直播回放.wav').write_bytes(b'synthetic replay')
        (self.case/'inputs/02_带时间码原稿.md').write_text('发言人1 00:00\n开场\n\n发言人1 20:00\n结束',encoding='utf-8')
        self.records=observations_for(self.case)
        self.folder=self.case/'_work/分流试听'

    def tearDown(self):self.temp.cleanup()

    def route(self,records=None):
        return build_route(self.case,acoustic_speakers='1',evidence=[],observations=self.records if records is None else records)

    def test_valid_positions_are_bound_to_source_clips_and_manifest(self):
        route=self.route();self.assertEqual(route['schema_version'],2)
        self.assertEqual(route_position_errors(self.case,route),[])
        self.assertEqual(len(route['replay']['observations']),5)
        self.assertEqual(route['replay']['probe_manifest_sha256'],file_sha256(self.folder/'manifest.json'))

    def test_five_opening_records_cannot_masquerade_as_full_coverage(self):
        records=copy.deepcopy(self.records)
        for i,row in enumerate(records):row.update(position='开场',finding=f'合成测试不同文字{i}')
        with self.assertRaises(ValueError):self.route(records)
        with self.assertRaises(ValueError):build_route(self.case,acoustic_speakers='1',evidence=[f'00:01|文字{i}' for i in range(5)])

    def test_time_ranges_must_match_the_five_windows(self):
        records=copy.deepcopy(self.records)
        records[2]['start_seconds']=1;records[2]['end_seconds']=21
        with self.assertRaises(ValueError):self.route(records)

    def test_replaced_or_missing_clip_invalidates_observation(self):
        manifest=json.loads((self.folder/'manifest.json').read_text());path=self.folder/manifest['clips'][2]['file']
        path.write_bytes(b'different clip')
        with self.assertRaises(ValueError):self.route()
        path.unlink()
        with self.assertRaises(ValueError):self.route()

    def test_transcript_only_or_placeholder_evidence_rejected(self):
        for key,value in [('method','仅凭转写文本'),('method','待填写'),('finding','待确认')]:
            records=copy.deepcopy(self.records);records[1][key]=value
            with self.assertRaises(ValueError):self.route(records)

    def test_hand_edited_manifest_or_proof_does_not_reuse_route(self):
        route=self.route();route['replay']['observations'][2]['start_seconds']=1
        self.assertTrue(route_position_errors(self.case,route))
        route=self.route();(self.folder/'manifest.json').write_text((self.folder/'manifest.json').read_text()+'\n')
        self.assertTrue(route_position_errors(self.case,route))

    def test_unperformed_or_inconclusive_checks_cannot_route_as_pass(self):
        for key,value in [('method','尚未进行真人回听'),('method','准备进行音频理解'),
                          ('finding','听不清，无法判断是否还有其他说话人'),
                          ('finding','无法确认内容说话人数')]:
            records=copy.deepcopy(self.records);records[2][key]=value
            with self.assertRaises(ValueError,msg=value):self.route(records)
        records=copy.deepcopy(self.records)
        for row in records:row['finding']='合成测试：只见同一位内容说话人，未发现第二位'
        self.assertEqual(self.route(records)['status'],'PASS')

    def test_distinct_filenames_cannot_hide_one_short_window(self):
        path=self.folder/'manifest.json';data=json.loads(path.read_text())
        data['source_duration_seconds']=10
        records=copy.deepcopy(self.records)
        for clip,row in zip(data['clips'],records):
            clip.update(start_seconds=0,duration_seconds=10)
            row.update(start_seconds=0,end_seconds=10)
        path.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaises(ValueError):self.route(records)

    def test_legacy_record_does_not_gain_new_handoff_proof(self):
        self.assertEqual(route_position_errors(self.case,{'schema_version':1}),[])
        self.assertTrue(route_position_errors(self.case,{'schema_version':1},handoff=True))

    def test_duplicate_short_windows_are_not_five_independent_checks(self):
        path=self.folder/'manifest.json';data=json.loads(path.read_text())
        for row in data['clips']:row['file']=data['clips'][0]['file']
        path.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaises(ValueError):self.route()


if __name__=='__main__':unittest.main()
