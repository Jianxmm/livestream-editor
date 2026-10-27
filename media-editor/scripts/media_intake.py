#!/usr/bin/env python3
"""Optional, local-first media intake and resumable whisper.cpp transcription."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
    return h.hexdigest()


def required(name: str) -> str:
    result=shutil.which(name)
    if not result:raise ValueError('找不到工具 '+name+'；请按使用说明准备，或提供已有逐字稿。')
    return result


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,check=False)


def probe(media: Path) -> float:
    result=run([required('ffprobe'),'-v','error','-show_entries','format=duration:stream=codec_type',
                '-of','json',str(media)])
    if result.returncode:raise ValueError('无法读取媒体；请检查格式和文件是否完整。')
    info=json.loads(result.stdout)
    if not any(row.get('codec_type')=='audio' for row in info.get('streams',[])):
        raise ValueError('媒体没有可用音轨。')
    seconds=float(info.get('format',{}).get('duration',0))
    if not math.isfinite(seconds) or seconds<=0:raise ValueError('无法确定媒体时长。')
    return seconds


def clean_url(value: str) -> str:
    parsed=urlsplit(value)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('链接必须是无内嵌凭据的 http(s) 单条素材链接。')
    # Preserve only a verified single Bilibili part selector. Other parameters
    # can contain credentials or signatures and are deliberately discarded.
    query=''
    host=parsed.hostname.lower()
    if host=='bilibili.com' or host.endswith('.bilibili.com'):
        parts=[v for k,v in parse_qsl(parsed.query,keep_blank_values=True) if k=='p']
        if len(parts)==1 and parts[0].isascii() and parts[0].isdigit() and 1<=int(parts[0])<=999999:
            query=urlencode({'p':str(int(parts[0]))})
    return urlunsplit((parsed.scheme,parsed.netloc,parsed.path,query,''))


def check_task(task: str) -> dict:
    needs={'text':[], 'subtitle':['python3'], 'url':['yt-dlp','ffprobe'],
           'local-asr':['ffmpeg','ffprobe','whisper-cli']}[task]
    found={name:bool(shutil.which(name)) for name in needs}
    return {'task':task,'tools':found,'ready':all(found.values()),
            'note':'仅检查工具；模型、平台权限及实际转写质量需单独核对。'}


def fetch(url: str, output: Path, mode: str, subtitle_language: str='zh') -> dict:
    public_url=clean_url(url); ytdlp=required('yt-dlp')
    if mode!='subtitle':required('ffprobe')
    if output.exists():raise ValueError('输出目录已存在；为避免覆盖请选择新目录。')
    # --no-playlist alone does not reject playlist-only URLs, so inspect the
    # resolved metadata first. Raw metadata may contain signed URLs: never save it.
    meta=run([ytdlp,'--ignore-config','--no-playlist','--skip-download','--dump-single-json',url])
    if meta.returncode:raise ValueError('无法读取该链接；请检查平台访问、登录和授权，或提供文件。')
    info=json.loads(meta.stdout)
    if not isinstance(info,dict) or info.get('_type') in {'playlist','multi_video'} or 'entries' in info:
        raise ValueError('该链接不是单条素材；请提供明确的单条链接。')
    subtitle_kind=None
    if mode=='subtitle':
        # Exact selection only: do not silently use a different language.
        if subtitle_language in info.get('subtitles',{}):subtitle_kind='human_or_platform'
        elif subtitle_language in info.get('automatic_captions',{}):subtitle_kind='automatic'
        else:raise ValueError('未找到指定语言字幕；可选择其他实际可用语言或提取音频。')
    output.mkdir(parents=True)
    template=str(output/'media.%(ext)s')
    if subtitle_kind:
        flag='--write-subs' if subtitle_kind=='human_or_platform' else '--write-auto-subs'
        command=[ytdlp,'--ignore-config','--no-playlist','--no-overwrites','--skip-download',flag,
                 '--sub-langs',subtitle_language,'--sub-format','srt/vtt/best','-o',template,url]
    else:
        fmt='bestaudio/best' if mode=='audio' else 'bestvideo*+bestaudio/best'
        command=[ytdlp,'--ignore-config','--no-playlist','--no-overwrites','-f',fmt,'-o',template,url]
    result=run(command)
    media=[p for p in output.iterdir() if p.is_file() and not p.name.endswith(('.part','.ytdl'))]
    if result.returncode or len(media)!=1:
        raise ValueError('素材未完整获取；保留现场，不绕过平台限制或覆盖文件。')
    seconds=None if subtitle_kind else probe(media[0])
    record={'status':'media_available','source':public_url,'source_id':info.get('id'),
            'title':info.get('title'),'media':media[0].name,'media_sha256':digest(media[0]),
            'duration_seconds':seconds,'kind':mode,'subtitle_kind':subtitle_kind,
            'note':'字幕来源不代表准确或完整，尚未回听。' if subtitle_kind else '只验证音轨和时长，尚未转写或回听。'}
    (output/'source.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return record


def validate_segment(row: dict, chunk_ms: int) -> tuple[int,int,str]:
    offsets=row.get('offsets',{});a=offsets.get('from');b=offsets.get('to');text=row.get('text','')
    if isinstance(a,bool) or isinstance(b,bool) or not isinstance(a,int) or not isinstance(b,int):
        raise ValueError('转写分段缺少可信毫秒时间，保留现场。')
    if a<0 or a>=chunk_ms or b<=a or b>chunk_ms+1500 or not isinstance(text,str):
        raise ValueError('转写分段时间无效或超出区间，保留现场。')
    return a,b,text.strip()


def stamp(ms: int) -> str:
    h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def write_once_or_equal(path: Path, content: bytes):
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes()!=content:
            raise ValueError('已有不同结果，拒绝覆盖：'+path.name)
        return
    with path.open('xb') as f:f.write(content)


def rows_digest(rows: list) -> str:
    return hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def coverage_candidates(rows: list[dict], duration_ms: int) -> list[dict]:
    """Return intervals outside recognized speech, never claim speech is missing."""
    intervals=sorted((max(0,r['start_ms']),min(duration_ms,r['end_ms'])) for r in rows
                     if r['end_ms']>r['start_ms'] and r['start_ms']<duration_ms and r['end_ms']>0)
    if not intervals:return []  # No recognized text is handled as a blocked ASR result.
    gaps=[];cursor=0;has_interval=False
    for start,end in intervals:
        kind='中间' if has_interval else '开头';threshold=30000 if has_interval else 5000
        if start-cursor>=threshold:gaps.append({'kind':kind,'start_ms':cursor,'end_ms':start})
        cursor=max(cursor,end);has_interval=True
    if duration_ms-cursor>=5000:gaps.append({'kind':'结尾','start_ms':cursor,'end_ms':duration_ms})
    return gaps


def source_clip(ffmpeg: str, media: Path, output: Path, name: str,
                start: int, end: int, source_sha256: str) -> dict:
    target=output/name
    with tempfile.TemporaryDirectory(prefix='asr-evidence-') as temp:
        clip=Path(temp)/'clip.wav'
        converted=run([ffmpeg,'-nostdin','-v','error','-ss',f'{start/1000:.3f}',
                       '-t',f'{(end-start)/1000:.3f}','-i',str(media),'-vn','-ar','16000','-ac','1',str(clip)])
        if converted.returncode or not clip.is_file():raise ValueError('回听片段生成失败，保留已完成分段。')
        if digest(media)!=source_sha256:raise ValueError('媒体在处理期间发生变化。')
        write_once_or_equal(target,clip.read_bytes())
    return {'audio':name,'audio_sha256':digest(target),'start_ms':start,'end_ms':end}


def transcribe(media: Path, output: Path, model: Path, language: str, chunk_seconds: int, threads: int,
               cli: str='whisper-cli') -> dict:
    media=media.expanduser().resolve();model=model.expanduser().resolve()
    if not media.is_file() or not model.is_file():raise ValueError('媒体或本地模型文件不存在。')
    if not 30<=chunk_seconds<=3600 or not 1<=threads<=64:raise ValueError('分段秒数须为 30–3600，线程须为 1–64。')
    whisper=required(cli);ffmpeg=required('ffmpeg');duration=probe(media)
    version=run([whisper,'--version'])
    if version.returncode:raise ValueError('无法读取 whisper-cli 版本；请检查工具。')
    binding={'media_sha256':digest(media),'model_sha256':digest(model),'language':language,
             'chunk_seconds':chunk_seconds,'duration_ms':round(duration*1000),
             'engine':'whisper.cpp','engine_sha256':digest(Path(whisper).resolve())}
    existed=output.exists()
    if existed and (not output.is_dir() or output.is_symlink()):raise ValueError('输出路径不是可用的普通目录。')
    if not existed:output.mkdir(parents=True)
    lock=output/'.transcription.lock'
    try:fd=os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise ValueError('转写目录被锁定；请确认没有转写进程后再处理锁文件。') from None
    try:
        with os.fdopen(fd,'w') as f:f.write(str(os.getpid()))
        marker=output/'source.json'
        if marker.exists():
            if marker.is_symlink() or json.loads(marker.read_text(encoding='utf-8'))!=binding:
                raise ValueError('已有转写输入或配置不同，拒绝覆盖或混用。')
        else:
            if any(p.name!='.transcription.lock' for p in output.iterdir()):
                raise ValueError('已有目录包含未绑定文件，拒绝覆盖。')
            write_once_or_equal(marker,(json.dumps(binding,ensure_ascii=False,indent=2)+'\n').encode())
        count=math.ceil(binding['duration_ms']/(chunk_seconds*1000));all_rows=[];reused=0
        for index in range(count):
            start=index*chunk_seconds*1000;span=min(chunk_seconds*1000,binding['duration_ms']-start)
            part=output/f'part-{index+1:04d}.json'
            if part.exists():
                if part.is_symlink():raise ValueError('分段文件不能为符号链接。')
                saved=json.loads(part.read_text(encoding='utf-8'))
                if saved.get('binding')!=binding or saved.get('start_ms')!=start or saved.get('duration_ms')!=span:
                    raise ValueError('已有分段与输入不一致，拒绝复用。')
                rows=saved.get('segments');reused+=1
                if not isinstance(rows,list) or saved.get('segments_sha256')!=rows_digest(rows):
                    raise ValueError('已有分段内容发生变化，拒绝复用。')
            else:
                # Incomplete engine output lives in a temporary directory; only
                # fully validated segments become reusable part files.
                with tempfile.TemporaryDirectory(prefix='asr-chunk-') as temp:
                    wav=Path(temp)/'audio.wav';stem=Path(temp)/'result'
                    conv=run([ffmpeg,'-nostdin','-v','error','-ss',f'{start/1000:.3f}',
                              '-t',f'{span/1000:.3f}','-i',str(media),'-vn','-ar','16000','-ac','1',str(wav)])
                    if conv.returncode or not wav.is_file():raise ValueError('音轨提取失败；已完成的分段保留。')
                    res=run([whisper,'-m',str(model),'-f',str(wav),'-l',language,'-t',str(threads),'-oj','-of',str(stem),'-np'])
                    if res.returncode or not stem.with_suffix('.json').is_file():raise ValueError('本地转写失败；已完成的分段保留。')
                    generated=json.loads(stem.with_suffix('.json').read_text(encoding='utf-8'))
                    rows=generated.get('transcription')
                    if not isinstance(rows,list):raise ValueError('转写输出缺少分段。')
                    for row in rows:validate_segment(row,span)
                    if digest(media)!=binding['media_sha256'] or digest(model)!=binding['model_sha256']:
                        raise ValueError('媒体或模型在处理期间发生变化。')
                    saved={'binding':binding,'start_ms':start,'duration_ms':span,'segments':rows,'segments_sha256':rows_digest(rows)}
                    write_once_or_equal(part,(json.dumps(saved,ensure_ascii=False,indent=2)+'\n').encode())
            if not isinstance(rows,list):raise ValueError('已有分段无效。')
            for row in rows:
                a,b,text=validate_segment(row,span)
                if text:all_rows.append({'start_ms':start+a,'end_ms':min(start+b,binding['duration_ms']),
                                         'text':text,'chunk':index+1})
        if not all_rows:raise ValueError('没有识别到文字；请检查音轨、语言及静音。')
        if digest(media)!=binding['media_sha256'] or digest(model)!=binding['model_sha256']:
            raise ValueError('媒体或模型在处理期间发生变化。')
        # Every hard cut can truncate a phrase. Export a source-linked listening
        # clip and both nearby hypotheses without changing the main transcript.
        boundaries=[]
        for number in range(1,count):
            cut=number*chunk_seconds*1000;start=max(0,cut-5000);end=min(binding['duration_ms'],cut+5000)
            filename=f'boundary-{number:04d}.wav'
            clip_info=source_clip(ffmpeg,media,output,filename,start,end,binding['media_sha256'])
            left=[r for r in all_rows if r['chunk']==number and r['end_ms']>start]
            right=[r for r in all_rows if r['chunk']==number+1 and r['start_ms']<end]
            boundaries.append({'cut_ms':cut,'start_ms':start,'end_ms':end,'audio':filename,
                               'audio_sha256':clip_info['audio_sha256'],'left':left,'right':right,'status':'待回听'})
        gaps=coverage_candidates(all_rows,binding['duration_ms'])
        coverage=[]
        for index,gap in enumerate(gaps,1):
            start,end=gap['start_ms'],gap['end_ms']
            windows=[(start,end)] if end-start<=20000 else [(start,start+10000),(end-10000,end)]
            clips=[source_clip(ffmpeg,media,output,f'coverage-{index:04d}-{n:02d}.wav',a,b,
                               binding['media_sha256']) for n,(a,b) in enumerate(windows,1)]
            coverage.append(dict(gap,clips=clips,status='待核；可能为静音、音乐或未识别语音'))
        # Preserve actual engine timestamps and words, including repetitions.
        srt=[]
        for i,row in enumerate(all_rows,1):
            srt.append(f"{i}\n{stamp(row['start_ms'])} --> {stamp(row['end_ms'])}\n{row['text']}\n\n")
        text='\n'.join(row['text'] for row in all_rows)+'\n'
        result={'status':'transcribed_needs_review','binding':binding,'segments':all_rows,
                'speaker':'未标注，不能作为说话人身份或已回听证据',
                'boundaries':boundaries,
                'note':'分段转写可能在边界漏字、重复或发生幻觉；须按实际原声核对。'}
        review=['# 转写分段交界待核\n\n','以下为机器切分与识别线索，没有自动校正或听清。请回听片段；切点前后可能漏字、重复或错词。\n\n']
        if not boundaries:review.append('本次只有一个分段，没有分段交界；不代表识别结果已核实。\n')
        for b in boundaries:
            review.append(f"## 切点 {stamp(b['cut_ms'])}\n\n")
            review.append(f"[回听原声片段]({b['audio']})（原视频 {stamp(b['start_ms'])} 至 {stamp(b['end_ms'])}）\n\n")
            for label,rows in [('切点之前',b['left']),('切点之后',b['right'])]:
                review.append(label+'：\n\n')
                review.extend(f"- {stamp(row['start_ms'])}：{row['text']}\n" for row in rows)
                if not rows:review.append('- 本侧未识别到文字；需要听原声确认。\n')
                review.append('\n')
        coverage_text=['# 转写时间覆盖候选\n\n',
                       '以下是识别文字时间区间之外的候选，可能是静音、音乐、时间偏移或未识别语音，不证明缺段。'
                       '短片只覆盖标注的局部，不能代表整个空档；如有疑点回到完整原音频继续检查。\n\n']
        if not coverage:coverage_text.append('没有达到当前阈值的空白候选；不代表原稿完整或已听清。\n')
        for number,gap in enumerate(coverage,1):
            coverage_text.append(f"## {number}. {gap['kind']} {stamp(gap['start_ms'])} 至 {stamp(gap['end_ms'])}\n\n")
            for clip in gap['clips']:
                coverage_text.append(f"- [回听局部]({clip['audio']})：{stamp(clip['start_ms'])} 至 {stamp(clip['end_ms'])}\n")
            coverage_text.append('\n状态：待核；不能仅凭空白认定漏转。\n\n')
        write_once_or_equal(output/'transcript.srt',''.join(srt).encode())
        write_once_or_equal(output/'transcript.txt',text.encode())
        write_once_or_equal(output/'transcript.json',(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
        write_once_or_equal(output/'boundary-review.json',(json.dumps({'source_sha256':binding['media_sha256'],
                            'boundaries':boundaries},ensure_ascii=False,indent=2)+'\n').encode())
        write_once_or_equal(output/'boundary-review.md',''.join(review).encode())
        write_once_or_equal(output/'coverage-review.json',(json.dumps({'source_sha256':binding['media_sha256'],
                            'duration_ms':binding['duration_ms'],'candidates':coverage},ensure_ascii=False,indent=2)+'\n').encode())
        write_once_or_equal(output/'coverage-review.md',''.join(coverage_text).encode())
        return {'status':result['status'],'segments':len(all_rows),'chunks':count,'reused_chunks':reused,
                'srt':str(output/'transcript.srt'),'boundary_review':str(output/'boundary-review.md'),
                'coverage_review':str(output/'coverage-review.md'),'coverage_candidate_count':len(coverage),
                'coverage_candidates':[{'kind':g['kind'],'start_ms':g['start_ms'],'end_ms':g['end_ms']} for g in coverage],
                'note':result['note']}
    finally:
        lock.unlink(missing_ok=True)


def main() -> int:
    parser=argparse.ArgumentParser(description='单条素材获取和可续用的本地转写')
    sub=parser.add_subparsers(dest='command',required=True)
    check=sub.add_parser('doctor');check.add_argument('--task',choices=['text','subtitle','url','local-asr'],required=True)
    get=sub.add_parser('fetch');get.add_argument('url');get.add_argument('--output-dir',type=Path,required=True)
    get.add_argument('--kind',choices=['video','audio','subtitle'],default='video')
    get.add_argument('--subtitle-language',default='zh',help='字幕实际语言代码；先选人工字幕，再尝试自动字幕')
    asr=sub.add_parser('transcribe');asr.add_argument('media',type=Path);asr.add_argument('--output-dir',type=Path,required=True)
    asr.add_argument('--model',type=Path,required=True);asr.add_argument('--language',default='auto')
    asr.add_argument('--chunk-seconds',type=int,default=600);asr.add_argument('--threads',type=int,default=4)
    args=parser.parse_args()
    try:
        if args.command=='doctor':result=check_task(args.task)
        elif args.command=='fetch':result=fetch(args.url,args.output_dir,args.kind,args.subtitle_language)
        else:result=transcribe(args.media,args.output_dir,args.model,args.language,args.chunk_seconds,args.threads)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        print(json.dumps({'status':'blocked','reason':str(exc)},ensure_ascii=False));return 1

if __name__=='__main__':raise SystemExit(main())
