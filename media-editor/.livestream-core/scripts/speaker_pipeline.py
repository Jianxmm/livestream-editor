"""Generic local diarization and source alignment; never invents identity anchors."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

from media_tools import audio_clip, duration
from route_livestream import find_replay, file_sha256, SPEAKER_HEADER_RE, valid_default_map
from setup_speakers import default_runtime, runtime_python
from workspace import ROOT, write_json
from source_turns import CONTRACT,blocks_from_text,contract_state


def seconds(value: str) -> float:
    parts=[int(v) for v in value.split(':')]
    if len(parts) not in (2,3) or any(v<0 for v in parts) or parts[-1]>=60 or parts[-2]>=60:
        raise ValueError('无效时间码：'+value)
    return float(sum(v*60**i for i,v in enumerate(reversed(parts))))


def timecode(value: float) -> str:
    value=int(value);return f'{value//3600:02d}:{value//60%60:02d}:{value%60:02d}'


def validate_calibration(data: dict, length: float) -> None:
    confirmed={'已确认','已回听','真人已确认','已当事人确认'}
    speakers=data.get('speakers',[])
    if data.get('status') not in confirmed or len(speakers)!=2:
        raise ValueError('请先根据回放确认两人的真实身份及声音锚点，再运行 speakers。')
    ids=set(); ranges=[]
    for person in speakers:
        sid=person['speaker_id'];name=person['name'];refs=person.get('reference_segments',[])
        if not sid or sid in ids or not name or '姓名' in name or len(refs)<2:raise ValueError('身份或锚点不完整')
        ids.add(sid);total=0
        for ref in refs:
            start,end=seconds(ref['start']),seconds(ref['end'])
            if not 0<=start<end<=length:raise ValueError('锚点超出实际回放时长')
            basis=ref.get('identity_basis','')
            if ref.get('review_status') not in confirmed or ref.get('overlap') is not False or not basis or any(x in basis for x in ['填写','上下文','原转写标签']):
                raise ValueError('锚点必须有回放声音/画面或真人确认依据，不能使用原稿标签或上下文猜测')
            total+=end-start;ranges.append((start,end,sid))
        if total<30:raise ValueError('每位说话人至少需要累计30秒干净声音锚点')
    for i,(a,b,sid) in enumerate(ranges):
        if any(a<d and c<b for c,d,_ in ranges[i+1:]):
            raise ValueError('身份锚点互相重叠或重复，不能重复计算时长')
    if not valid_default_map(data.get('source_label_default_map'), ids):
        raise ValueError('普通标签映射须覆盖两位身份；发言人未标注必须设为 null，不能猜测身份。映射只是对照假设。')


def align_blocks(source: str, segments: list[dict], mapping: dict, length: float, *, allow_same_second: bool = False) -> tuple[list, list]:
    matches=list(SPEAKER_HEADER_RE.finditer(source));blocks=[];conflicts=[]
    identities=blocks_from_text(source) if allow_same_second else None
    for i,m in enumerate(matches):
        start=seconds(m.group('time'));end=seconds(matches[i+1].group('time')) if i+1<len(matches) else length
        tied=bool(identities and identities[i]['same_second'])
        if allow_same_second:
            end=next((seconds(n.group('time')) for n in matches[i+1:] if seconds(n.group('time'))>start),length)
        if end<=start or start>=length:raise ValueError('原稿时间码重复、倒序或超出音频')
        label=m.group('label').replace(' ','')
        linked=[s for s in segments if s['start_seconds']<end and s['end_seconds']>start]
        ids={s['speaker_id'] for s in linked}
        ambiguous=any(s.get('uncertain') or s.get('overlap') or s['end_seconds']-s['start_seconds']<3 for s in linked)
        if tied:status='无法映射'
        elif not linked: status='无法映射'
        elif len(ids)>1:status='混合说话人'
        elif ambiguous:status='无法映射'
        elif label in mapping and mapping[label] is None and all(s.get('uncertain') is False for s in linked):status='声学归属'
        elif next(iter(ids))!=mapping.get(label):status='冲突待核'
        else:status='一致'
        sid=next(iter(ids)) if len(ids)==1 and not tied else None
        cid=None
        if status not in {'一致','声学归属'}:
            cid=f'C{len(conflicts)+1:04d}'
            conflicts.append({'id':cid,'time':m.group('time'),'type':status,'label':label,'speakers':','.join(sorted(ids)) or '无声学覆盖'})
        extra=({'source_block_id':identities[i]['id'],'timing_ambiguous':tied,'retrieval_start_seconds':start,'retrieval_end_seconds':end} if identities else {})
        blocks.append({**extra,'source_start':m.group('time'),'source_label':label,'timeline_segment_ids':[s['id'] for s in linked],
                       'resolved_speaker_id':sid,'status':status,'conflict_id':cid,'notes':'同秒时间不提供精确语音边界；检索区间不是发言时长。' if tied else '机器对齐；声学归属不等于真人回听，疑点需回放核验'})
    if not blocks:raise ValueError('原稿没有可对齐的发言时间块')
    return blocks,conflicts


def infer(wav: Path, calibration: dict, runtime: Path, *, chunk_seconds: int = 600) -> list[dict]:
    import numpy as np
    import sherpa_onnx as so
    models=runtime/'models'
    lock=json.loads((ROOT/'assets/models.json').read_text(encoding='utf-8'))
    for item in lock['models']:
        if file_sha256(models/item['output'])!=item['model_sha256']:raise ValueError('声学模型哈希不匹配，请运行 setup-speakers')
    config=so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(models/'segmentation.onnx')),num_threads=2),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(models/'embedding.onnx'),num_threads=2),
        clustering=so.FastClusteringConfig(num_clusters=2,threshold=.5),min_duration_on=.3,min_duration_off=.5)
    if not config.validate():raise ValueError('声学模型配置无效')
    diarizer=so.OfflineSpeakerDiarization(config)
    extractor=so.SpeakerEmbeddingExtractor(so.SpeakerEmbeddingExtractorConfig(model=str(models/'embedding.onnx'),num_threads=2))
    def read(start,end):
        with wave.open(str(wav),'rb') as w:
            rate=w.getframerate();w.setpos(min(w.getnframes(),int(start*rate)))
            return np.frombuffer(w.readframes(int((end-start)*rate)),dtype='<i2').astype(np.float32)/32768
    def embedding(samples):
        stream=extractor.create_stream();stream.accept_waveform(sample_rate=16000,waveform=samples);stream.input_finished()
        if not extractor.is_ready(stream):raise ValueError('声音片段太短，无法生成可靠声纹')
        value=np.asarray(extractor.compute(stream),dtype=np.float32)
        return value/max(float(np.linalg.norm(value)),1e-10)
    anchors={p['speaker_id']:embedding(np.concatenate([read(seconds(r['start']),seconds(r['end'])) for r in p['reference_segments']])) for p in calibration['speakers']}
    with wave.open(str(wav),'rb') as w:length=w.getnframes()/w.getframerate()
    output=[]
    for core in range(0,math.ceil(length),chunk_seconds):
        end=min(length,core+chunk_seconds);left=max(0,core-5);right=min(length,end+5)
        samples=read(left,right);result=diarizer.process(samples).sort_by_start_time()
        print(f'声学处理 {core:.0f}–{end:.0f} / {length:.0f} 秒',flush=True)
        for segment in result:
            start,end_s=left+segment.start,left+segment.end
            start,end_s=max(float(core),start),min(end,end_s)
            if end_s<=start:continue
            local=samples[int(segment.start*16000):int(segment.end*16000)]
            try:
                vector=embedding(local)
                scores=sorted([(float(np.dot(vector,v)),sid) for sid,v in anchors.items()],reverse=True)
                score,sid=scores[0];margin=score-scores[1][0]
                uncertain=score<.3 or margin<.08
            except ValueError:
                # Keep short speech visible as unresolved, never silently delete it.
                sid='UNRESOLVED';score=0; margin=0;uncertain=True
            output.append({'id':f'S{len(output)+1:05d}','start':timecode(math.floor(start)),
                           'end':timecode(math.ceil(end_s)),'start_seconds':start,'end_seconds':end_s,
                           'raw_cluster':f'{core}:{segment.speaker}','speaker_id':sid,'overlap':False,
                           'uncertain':uncertain,'similarity':round(score,4),'margin':round(margin,4)})
    if not output:raise ValueError('未检测到可用语音，保留待核状态；不要按原稿标签伪造时间轴')
    # Integer display ranges can overlap after rounding; mark them explicitly too.
    for i,a in enumerate(output):
        for b in output[i+1:]:
            if seconds(b['start'])>=seconds(a['end']):break
            if a['speaker_id']!=b['speaker_id']:a['overlap']=b['overlap']=True
    return output


def validate_input_binding(case: Path, calibration: dict) -> None:
    replay=find_replay(case)
    if calibration.get('source_sha256')!=file_sha256(replay):
        raise ValueError('身份锚点未绑定当前回放哈希，请重新核对声音锚点')
    route=json.loads((case/'_work/场次分流.json').read_text(encoding='utf-8-sig'))
    if route.get('status')!='PASS' or route.get('mode')!='dual' or str(route.get('acoustic_speaker_count'))!='2':
        raise ValueError('双人声学处理需要先核实双人分流')
    for key,path in [('source',case/'inputs/02_带时间码原稿.md'),('replay',replay)]:
        if route.get(key,{}).get('sha256')!=file_sha256(path):
            raise ValueError('分流绑定的输入已变化，先重新核验')


def run(case: Path, runtime: Path) -> Path:
    case=case.resolve();runtime=runtime.expanduser().resolve()
    replay=find_replay(case);source=case/'inputs/02_带时间码原稿.md';cp=case/'_work/说话人校准.json'
    calibration=json.loads(cp.read_text(encoding='utf-8-sig'));length=duration(replay)
    validate_input_binding(case,calibration)
    validate_calibration(calibration,length)
    if calibration.get('source_audio')!=replay.relative_to(case).as_posix():raise ValueError('身份锚点未绑定本场回放路径')
    targets=[case/'_work'/n for n in ['说话人时间轴.json','说话人冲突清单.md','说话人待核项.md']]
    if any(p.exists() for p in targets):raise FileExistsError('已有说话人结果，拒绝覆盖可能包含人工结论的文件；请先比较并归档。')
    with tempfile.TemporaryDirectory(prefix='.speaker-',dir=case/'_work') as temp:
        wav=Path(temp)/'audio.wav';audio_clip(replay,wav,pcm=True)
        segments=infer(wav,calibration,runtime)
        ordered,errors=contract_state(case)
        if errors:raise ValueError('；'.join(errors))
        blocks,conflicts=align_blocks(source.read_text(encoding='utf-8-sig'),segments,calibration['source_label_default_map'],length,allow_same_second=ordered)
        import sherpa_onnx
        timeline={'version':1,'source_audio':replay.relative_to(case).as_posix(),'source_transcript':source.relative_to(case).as_posix(),
                  'speaker_count':2,'method':f'sherpa-onnx {sherpa_onnx.__version__} + 独立声音锚点逐段比对',
                  'calibration_file':'_work/说话人校准.json','segments':segments,'source_blocks':blocks,
                  'provenance':{'replay_sha256':file_sha256(replay),'transcript_sha256':file_sha256(source),'calibration_sha256':file_sha256(cp)},
                  'notes':'两人聚类以已核验的分流为前提，不用于证明只有两人；相似度阈值为保守筛查启发式，非准确率。'}
        if ordered:timeline['transcript_contract']=CONTRACT
        write_json(Path(temp)/targets[0].name,timeline)
        lines=['# 说话人冲突清单\n',f'- 未解决冲突：{len(conflicts)}\n','| 冲突编号 | 级别 | 时间码 | 冲突类型 | 原转写标签 | 独立时间轴 | 上下文提示 | 处理结论 | 状态 |','| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
        issues=['# 说话人待核项\n','将以下行合并到 outputs/02_待核问题清单.md，并更新汇总；不要覆盖已有人工结论。\n','| 级别 | 时间码 | 问题类型 | 原文/成稿 | 处理建议 | 状态 |','| --- | --- | --- | --- | --- | --- |']
        for c in conflicts:
            lines.append(f"| {c['id']} | P1 | {c['time']} | {c['type']} | {c['label']} | {c['speakers']} | 仅触发复核 | 回听后填写实际依据 | 待确认 |")
            issues.append(f"| P1 | {c['time']} | 说话人归属 | {c['id']} {c['type']} | 回听核验，正文暂不依赖该归属 | 待确认 |")
        (Path(temp)/targets[1].name).write_text('\n'.join(lines)+'\n',encoding='utf-8')
        (Path(temp)/targets[2].name).write_text('\n'.join(issues)+'\n',encoding='utf-8')
        for target in targets:(Path(temp)/target.name).rename(target)
    return targets[0]


def main() -> int:
    p=argparse.ArgumentParser(description='从回放独立生成双人时间轴；需先确认身份锚点')
    p.add_argument('case',type=Path);p.add_argument('--runtime',type=Path,default=default_runtime())
    p.add_argument('--check',action='store_true',help='只检查三件套，供正文编辑前使用')
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args()
    try:
        if a.check:
            a.case=a.case.resolve()
            calibration=json.loads((a.case/'_work/说话人校准.json').read_text(encoding='utf-8-sig'))
            validate_input_binding(a.case,calibration)
            timeline=json.loads((a.case/'_work/说话人时间轴.json').read_text(encoding='utf-8-sig'))
            for key,path in [('replay_sha256',find_replay(a.case)),('transcript_sha256',a.case/'inputs/02_带时间码原稿.md'),('calibration_sha256',a.case/'_work/说话人校准.json')]:
                if timeline.get('provenance',{}).get(key)!=file_sha256(path):raise ValueError('时间轴绑定已失效：'+key)
            from audit_draft import audit_speaker_preflight
            paths=[a.case/'_work'/n for n in ['说话人校准.json','说话人时间轴.json','说话人冲突清单.md']]
            findings=audit_speaker_preflight((a.case/'inputs/02_带时间码原稿.md').read_text(encoding='utf-8-sig'),
                (a.case/'outputs/02_待核问题清单.md').read_text(encoding='utf-8-sig'),
                *(p.read_text(encoding='utf-8-sig') for p in paths),*paths)
            for f in findings:print(f'{f.level} {f.code}: {f.message}')
            return int(any(f.level=='ERROR' for f in findings))
        if not a.worker:
            executable=runtime_python(a.runtime.expanduser().resolve())
            if not executable.is_file():raise RuntimeError('先运行 livestream.py setup-speakers 安装隔离的声学运行时')
            return subprocess.run([str(executable),str(Path(__file__).resolve()),str(a.case.resolve()),'--runtime',str(a.runtime.expanduser().resolve()),'--worker'],env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')).returncode
        print('SPEAKERS=GENERATED_PENDING_REVIEW\n'+str(run(a.case,a.runtime)));return 0
    except Exception as exc:
        print('SPEAKERS=BLOCKED\n'+str(exc),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
