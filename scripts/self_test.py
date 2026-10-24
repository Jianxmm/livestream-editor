"""Offline technical smoke test, using generated tones (not editorial evidence)."""
from __future__ import annotations
import argparse
import json
import math
import re
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import wave
from urllib.parse import unquote, urlsplit

from workspace import doctor, init_case, export_outputs, probe, ROOT
from media_tools import audio_clip, duration
from extract_full_audio import extract_full_audio
from build_source_review_index import build_source_review_index
from route_livestream import file_sha256, build_route, write_route, route_position_errors
from extract_review_clips import extract_review_package


def continuation_smoke(root: Path, short_media: Path) -> None:
    """Real encoding plus synthetic proof records, never human review evidence."""
    media=root/'长合成音调.wav'
    with wave.open(str(short_media),'rb') as source:
        params=source.getparams();frames=source.readframes(source.getnframes())
    with wave.open(str(media),'wb') as output:
        output.setparams(params)
        for _ in range(6):output.writeframes(frames)
    transcript=root/'续作原稿.md'
    transcript.write_text('发言人1 00:00\n合成技术测试开场。\n\n发言人1 00:10\n旧问题合成文字。\n\n发言人1 01:30\n新问题合成文字。\n\n发言人1 02:40\n合成技术测试结尾。\n',encoding='utf-8')
    case=init_case(root/'合成续作案例',media,transcript)
    folder=probe(case)
    records=json.loads((folder/'核验记录.json').read_text(encoding='utf-8'))
    for row in records:
        row.update(method='音频理解（合成测试记录，不是真人回听）',finding='合成夹具预设一个声音，仅测试记录链路，不构成真实人数证据')
    route=build_route(case,acoustic_speakers='1',evidence=[],observations=records)
    write_route(case,route)
    assert not route_position_errors(case,route)
    issue_path=case/'outputs/02_待核问题清单.md'
    header='| 级别 | 时间码 | 问题类型 | 原文/成稿 | 处理建议 | 状态 |\n| --- | --- | --- | --- | --- | --- |\n'
    old='| P1 | 00:10 | 技术样例 | 旧问题合成文字 | 合成核验 | 待确认 |\n'
    issue_path.write_text(header+old,encoding='utf-8')
    extract_review_package(case)
    manifest_path=case/'_work/review_clips/manifest.json'
    first=json.loads(manifest_path.read_text(encoding='utf-8'))
    sheet=case/'_work/回听终审表.md'
    filled=sheet.read_text(encoding='utf-8').replace('| 待填 | 待定 | 待填 |','| 合成核验文字 | 合成保留 | 合成测试人 / 2026-10-23 |')
    sheet.write_text(filled,encoding='utf-8')
    issue_path.write_text(header+old.replace('待确认','已回听')+'| P1 | 01:30 | 技术样例 | 新问题合成文字 | 合成核验 | 待确认 |\n',encoding='utf-8')
    extract_review_package(case)
    current=json.loads(manifest_path.read_text(encoding='utf-8'))
    old_clips=[row for row in current['clips'] if row['issue_text']=='旧问题合成文字']
    assert old_clips==first['clips']
    assert '合成测试人 / 2026-10-23' in sheet.read_text(encoding='utf-8')
    assert any(row['issue_text']=='新问题合成文字' and row['issue_index']!=old_clips[0]['issue_index'] for row in current['clips'])
    build_source_review_index(case)
    for name in ['01_公众号可发布稿.md','03_编辑质检报告.md']:
        (case/'outputs'/name).write_text('# 合成技术测试\n非实际编辑结果。',encoding='utf-8')
    exported=export_outputs(case,root/'独立核对阅读包')
    case.rename(root/'源案例已移走')
    assert list((exported/'核对材料').rglob('*.m4a'))
    for page in exported.rglob('*.md'):
        text=page.read_text(encoding='utf-8')
        assert '../_work/' not in text
        for target in re.findall(r'\]\(([^)]+)\)',text):
            parts=urlsplit(target)
            if not parts.scheme and parts.path:
                assert (page.parent/unquote(parts.path)).is_file(), (page,target)


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
        subtitle=root/'原字幕.srt'
        subtitle.write_bytes('1\r\n00:00:00,120 --> 00:00:00,600\r\n字幕技术样例，\r\n\r\n2\r\n00:00:00,700 --> 00:00:09,800\r\n不是真实讲话。\r\n\r\n3\r\n00:00:10,900 --> 00:00:29,000\r\n保留原字幕和准确时间。\r\n'.encode('utf-8-sig'))
        subtitle_case=init_case(root/'字幕导入样例',media,subtitle)
        assert doctor(subtitle_case)['status']=='READY'
        assert file_sha256(subtitle)==file_sha256(subtitle_case/'inputs/05_平台原始导出.srt')
        imported=json.loads((subtitle_case/'_work/字幕导入记录.json').read_text(encoding='utf-8'))
        assert imported['cue_count']==3 and imported['grouped_block_count']==1
        assert imported['blocks'][0]['cue_numbers']==[1,2]
        assert '字幕技术样例，\n不是真实讲话。' in (subtitle_case/'inputs/02_带时间码原稿.md').read_text(encoding='utf-8')
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
        continuation_smoke(root,media)
    return {'status':'PASS','checks':['中文空格路径','输入不变','SRT导入及真实回放时长匹配','整场音频时长及哈希','音频幂等复用','短音频真实转换','回听包重复生成及输入变更保护','原文索引','人工修改保护','五位置实际编码和结构化分流','关闭旧问题后追加新问题','移走源案例后的阅读核对链接'],
            'scope':'技术链路自检；合成音调不证明说话人分离准确率、文稿质量或真人终审完成。'}

def main() -> int:
    argparse.ArgumentParser(description='运行离线技术自检及回归测试，不读取真实案例。').parse_args()
    try:
        result=smoke()
        tests=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(ROOT/'tests'),'-p','test_*.py'])
        if tests.returncode:raise RuntimeError('回归测试失败')
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except Exception as exc:print('SELF_TEST=FAIL\n'+str(exc),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
