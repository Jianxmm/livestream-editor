import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/media_intake.py'
spec=importlib.util.spec_from_file_location('media_intake',SCRIPT)
intake=importlib.util.module_from_spec(spec);spec.loader.exec_module(intake)


class MediaIntakeTests(unittest.TestCase):
    def test_text_doctor_does_not_need_transcription_tools(self):
        with patch.dict(os.environ,{'PATH':''}):
            self.assertTrue(intake.check_task('text')['ready'])
            self.assertFalse(intake.check_task('local-asr')['ready'])

    def test_media_range_and_safe_source_identity(self):
        with self.assertRaises(ValueError):intake.validate_segment({'offsets':{'from':1000,'to':1200},'text':'越界'},1000)
        with self.assertRaises(ValueError):intake.validate_segment({'offsets':{'from':1200,'to':1600},'text':'越界'},1000)
        self.assertEqual(intake.validate_segment({'offsets':{'from':900,'to':1200},'text':'末尾'},1000),(900,1200,'末尾'))
        self.assertEqual(intake.clean_url('https://www.bilibili.com/video/BVtest?p=2&token=secret'),
                         'https://www.bilibili.com/video/BVtest?p=2')
        self.assertEqual(intake.clean_url('https://evilbilibili.com/video/BVtest?p=2&token=secret'),
                         'https://evilbilibili.com/video/BVtest')
        self.assertEqual(intake.clean_url('https://www.bilibili.com/video/BVtest?p=2&p=3&token=secret'),
                         'https://www.bilibili.com/video/BVtest')

    def test_coverage_unions_overlaps_and_uses_separate_edge_thresholds(self):
        rows=[{'start_ms':44000,'end_ms':50000},{'start_ms':6000,'end_ms':9000},
              {'start_ms':8000,'end_ms':12000},{'start_ms':42000,'end_ms':45000}]
        self.assertEqual(intake.coverage_candidates(rows,65000),[
            {'kind':'开头','start_ms':0,'end_ms':6000},
            {'kind':'中间','start_ms':12000,'end_ms':42000},
            {'kind':'结尾','start_ms':50000,'end_ms':65000}])
        self.assertEqual(intake.coverage_candidates([{'start_ms':4999,'end_ms':10000},
            {'start_ms':39999,'end_ms':55001}],60000),[])
        self.assertEqual(intake.coverage_candidates([],60000),[])

    def test_subtitles_from_single_url_and_playlist_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);exe=root/'yt-dlp'
            exe.write_text('''#!/usr/bin/env python3
import json, pathlib, sys
if '--dump-single-json' in sys.argv:
    print(json.dumps({'_type':'playlist','entries':[]} if 'playlist' in sys.argv[-1] else {'id':'x','title':'Test','subtitles':{'zh':[{}]}}))
else:
    out=sys.argv[sys.argv.index('-o')+1].replace('%(ext)s','zh.srt')
    pathlib.Path(out).write_text('1\\n00:00:00,000 --> 00:00:01,000\\n原话\\n')
''')
            exe.chmod(0o755)
            with patch.dict(os.environ,{'PATH':str(root)+os.pathsep+os.environ.get('PATH','')}):
                info=intake.fetch('https://example.test/one',root/'captions','subtitle','zh')
                self.assertEqual(info['subtitle_kind'],'human_or_platform')
                self.assertIn('原话',(root/'captions'/info['media']).read_text())
                with self.assertRaises(ValueError):intake.fetch('https://example.test/playlist',root/'many','subtitle','zh')
                self.assertFalse((root/'many').exists())
                with self.assertRaises(ValueError):intake.fetch('https://user:secret@example.test/one',root/'unsafe','subtitle','zh')
                self.assertFalse((root/'unsafe').exists())

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg is unavailable')
    def test_chunk_resume_keeps_timestamps_and_refuses_modified_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);media=root/'source.wav';model=root/'model.bin';out=root/'work';calls=root/'calls'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=66',
                            '-ar','16000','-ac','1',str(media)],check=True)
            original=intake.digest(media);model.write_bytes(b'fake model for deterministic adapter test')
            exe=root/'whisper-cli'
            exe.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
if '--version' in sys.argv:
    print('fixture 1'); sys.exit(0)
p=pathlib.Path(os.environ['ASR_TEST_COUNTER'])
n=len(p.read_text().splitlines())+1 if p.exists() else 1
with p.open('a') as f: f.write(str(n)+'\\n')
if os.environ.get('ASR_FAIL_ON') == str(n): sys.exit(2)
stem=sys.argv[sys.argv.index('-of')+1]
pathlib.Path(stem+'.json').write_text(json.dumps({'transcription':[{'offsets':{'from':0,'to':1000},'text':'原话'}]},ensure_ascii=False))
''')
            exe.chmod(0o755)
            env={'PATH':str(root)+os.pathsep+os.environ['PATH'],'ASR_TEST_COUNTER':str(calls),'ASR_FAIL_ON':'2'}
            with patch.dict(os.environ,env):
                with self.assertRaises(ValueError):intake.transcribe(media,out,model,'zh',30,2)
                self.assertTrue((out/'part-0001.json').exists())
                self.assertFalse((out/'transcript.srt').exists())
                os.environ.pop('ASR_FAIL_ON')
                result=intake.transcribe(media,out,model,'zh',30,2)
                self.assertEqual(result['reused_chunks'],1)
                self.assertEqual(len(calls.read_text().splitlines()),4)
                srt=(out/'transcript.srt').read_text()
                self.assertIn('00:00:30,000 --> 00:00:31,000',srt)
                self.assertIn('00:01:00,000 --> 00:01:01,000',srt)
                self.assertEqual(intake.digest(media),original)
                review=json.loads((out/'boundary-review.json').read_text())
                self.assertEqual(review['source_sha256'],original)
                self.assertEqual([(b['start_ms'],b['cut_ms'],b['end_ms']) for b in review['boundaries']],
                                 [(25000,30000,35000),(55000,60000,65000)])
                clip=out/'boundary-0001.wav';clip_bytes=clip.read_bytes()
                self.assertEqual(intake.digest(clip),review['boundaries'][0]['audio_sha256'])
                self.assertIn('00:00:30,000',(out/'boundary-review.md').read_text())
                coverage=json.loads((out/'coverage-review.json').read_text())
                self.assertEqual(coverage['source_sha256'],original)
                self.assertEqual(result['coverage_candidates'],[{'kind':'结尾','start_ms':61000,'end_ms':66000}])
                self.assertEqual(result['coverage_candidate_count'],1)
                tail=coverage['candidates'][0]['clips'][0]
                self.assertEqual((tail['start_ms'],tail['end_ms']),(61000,66000))
                self.assertEqual(intake.digest(out/tail['audio']),tail['audio_sha256'])
                self.assertIn('不证明缺段',(out/'coverage-review.md').read_text())
                old_outputs={name:intake.digest(out/name) for name in ('transcript.srt','transcript.txt','transcript.json')}
                # Re-running after an earlier version: new coverage sidecars are
                # generated without rewriting old transcript artifacts.
                for name in ('coverage-review.md','coverage-review.json',tail['audio']):(out/name).unlink()
                again=intake.transcribe(media,out,model,'zh',30,2)
                self.assertEqual(again['reused_chunks'],3)
                self.assertEqual(old_outputs,{name:intake.digest(out/name) for name in old_outputs})
                (out/tail['audio']).write_bytes(b'altered')
                with self.assertRaisesRegex(ValueError,'已有不同结果'):intake.transcribe(media,out,model,'zh',30,2)
                (out/tail['audio']).unlink()
                clip.write_bytes(b'edited or damaged')
                with self.assertRaisesRegex(ValueError,'已有不同结果'):intake.transcribe(media,out,model,'zh',30,2)
                clip.write_bytes(clip_bytes)
                saved=json.loads((out/'part-0001.json').read_text());saved['segments'][0]['text']='被修改'
                (out/'part-0001.json').write_text(json.dumps(saved,ensure_ascii=False))
                with self.assertRaisesRegex(ValueError,'发生变化'):intake.transcribe(media,out,model,'zh',30,2)
                self.assertIn('原话',srt)


if __name__=='__main__':unittest.main()
