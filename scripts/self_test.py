"""Offline technical smoke test, using generated tones (not editorial evidence)."""
from __future__ import annotations
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import wave

from workspace import doctor, init_case, export_outputs, ROOT
from media_tools import audio_clip, duration
from extract_full_audio import extract_full_audio
from build_source_review_index import build_source_review_index
from route_livestream import file_sha256


def smoke() -> dict:
    status=doctor()
    if status['status']!='READY':raise RuntimeError(json.dumps(status,ensure_ascii=False))
    with tempfile.TemporaryDirectory(prefix='直播 skill smoke ') as temp:
        root=Path(temp);media=root/'测试音频.wav'
        with wave.open(str(media),'wb') as w:
            w.setparams((1,2,16000,0,'NONE','not compressed'))
            one=b''.join(struct.pack('<h',int(2000*math.sin(2*math.pi*440*i/16000))) for i in range(16000))
            for _ in range(30):w.writeframes(one)
        source=root/'原稿.md';source.write_text('发言人1   00:00\n这是技术样例，不代表真实讲话。\n\n发言人1   00:10\n核验输入复制和索引定位。\n',encoding='utf-8')
        case=init_case(root/'2026-01-01-技术样例',media,source)
        before=file_sha256(case/'inputs/02_带时间码原稿.md')
        result=extract_full_audio(case)
        assert abs(result['output']['duration_seconds']-30)<1
        assert extract_full_audio(case)['reused'] is True
        issues=case/'outputs/02_待核问题清单.md'
        issues.write_text('# 待核问题清单\n\n| 级别 | 时间码 | 问题类型 | 原文/成稿 | 处理建议 | 状态 |\n| --- | --- | --- | --- | --- | --- |\n| P1 | 00:10 | 技术样例 | 索引测试 | 回听 | 待确认 |\n',encoding='utf-8')
        build_source_review_index(case)
        assert file_sha256(case/'inputs/02_带时间码原稿.md')==before
        cli=ROOT/'scripts/livestream.py'
        subprocess.run([sys.executable,str(cli),'review-clips',str(case)],check=True,capture_output=True)
        manifest=json.loads((case/'_work/review_clips/manifest.json').read_text(encoding='utf-8'))
        assert len(manifest['clips'])==1 and manifest['source_sha256']==file_sha256(case/'inputs/01_直播回放.wav')
        subprocess.run([sys.executable,str(cli),'review-clips',str(case)],check=True,capture_output=True)
        sheet=case/'_work/回听终审表.md';before_sheet=sheet.read_bytes()
        with (case/'inputs/01_直播回放.wav').open('ab') as f:f.write(b'changed')
        changed=subprocess.run([sys.executable,str(cli),'review-clips',str(case)],capture_output=True,text=True)
        assert changed.returncode!=0 and sheet.read_bytes()==before_sheet
        clip=root/'短音频.m4a' ;audio_clip(media,clip,10,5);assert abs(duration(clip)-5)<1
        for name in ['01_公众号可发布稿.md','03_编辑质检报告.md']:(case/'outputs'/name).write_text('技术测试文件，非编辑结果。',encoding='utf-8')
        dest=export_outputs(case,root/'阅读副本')
        edited=dest/'01_公众号可发布稿.md';edited.write_text('模拟人工修改',encoding='utf-8')
        try:export_outputs(case,dest)
        except FileExistsError:pass
        else:raise AssertionError('导出覆盖保护未生效')
        assert edited.read_text(encoding='utf-8')=='模拟人工修改'
    return {'status':'PASS','checks':['中文空格路径','输入不变','整场音频时长及哈希','音频幂等复用','短音频真实转换','回听包重复生成及输入变更保护','原文索引','人工修改保护'],
            'scope':'技术链路自检；合成音调不证明说话人分离准确率、文稿质量或真人终审完成。'}

def main() -> int:
    try:
        result=smoke()
        tests=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(ROOT/'tests'),'-p','test_*.py'])
        if tests.returncode:raise RuntimeError('回归测试失败')
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except Exception as exc:print('SELF_TEST=FAIL\n'+str(exc),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
