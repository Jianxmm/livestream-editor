"""Shared provenance gate, followed by the independent solo/dual editorial audit."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from route_livestream import file_sha256, find_replay


def matching_review_rows(sheet: str, item: dict) -> list[str]:
    # A single audio window may support multiple distinct factual issues.
    # Each issue needs its own conclusion, even when the filename is shared.
    key=item['timecode'] if item.get('priority')=='SAMPLE' else str(item['issue_index'])
    return [line for line in sheet.splitlines() if line.startswith('|')
            and f"`{item['filename']}`" in line
            and line.strip('|').split('|')[0].strip()==key]


def shared_errors(case: Path, *, handoff: bool = False) -> tuple[str, list[str]]:
    errors = []
    mode = ''
    try:
        route = json.loads((case / '_work/场次分流.json').read_text(encoding='utf-8-sig'))
        mode = route.get('mode', '')
        if route.get('status') != 'PASS' or mode not in {'solo','dual'}:
            errors.append('分流尚未通过或人数不受支持')
        if str(route.get('acoustic_speaker_count')) != ('1' if mode == 'solo' else '2'):
            errors.append('分流模式与声学人数不一致')
        replay = find_replay(case)
        for item, path in [(route['source'], case/'inputs/02_带时间码原稿.md'),(route['replay'],replay)]:
            if item.get('path') != path.relative_to(case).as_posix() or item.get('sha256') != file_sha256(path):
                errors.append(f'分流所绑定的输入已变化：{path.name}')
        evidence = route['replay'].get('evidence', [])
        if len(evidence) < 5 or len(set(evidence)) < 5:
            errors.append('必须提供五条不同的分流回放证据')
        index = (case/'_work/待核原文索引.md').read_text(encoding='utf-8-sig')
        for key, path in [('source',case/'inputs/02_带时间码原稿.md'),('issues',case/'outputs/02_待核问题清单.md')]:
            if f'<!-- {key}_sha256: {file_sha256(path)} -->' not in index:
                errors.append(f'原文索引未绑定当前 {key}，请重新生成索引')
        if mode == 'dual':
            timeline = json.loads((case/'_work/说话人时间轴.json').read_text(encoding='utf-8-sig'))
            binding = timeline.get('provenance',{})
            for key,path in [('replay_sha256',replay),('transcript_sha256',case/'inputs/02_带时间码原稿.md'),('calibration_sha256',case/'_work/说话人校准.json')]:
                if binding.get(key) != file_sha256(path): errors.append(f'说话人时间轴与当前输入不匹配：{key}')
        if handoff:
            manifest_path = case/'_work/review_clips/manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
            if manifest.get('transcript_sha256') != file_sha256(case/'inputs/02_带时间码原稿.md'):
                errors.append('回听音频未绑定当前原稿')
            if manifest.get('source_sha256') != file_sha256(replay):
                errors.append('回听音频未绑定当前回放')
            samples = manifest.get('samples', [])
            if len({x['filename'] for x in samples}) < 5:
                errors.append('真人终审需要至少五段不同的随机抽听片段')
            sheet = (case/'_work/回听终审表.md').read_text(encoding='utf-8-sig')
            for item in manifest.get('clips', []) + samples:
                name = item['filename']
                if Path(name).name != name:
                    errors.append('回听清单存在越界文件名'); continue
                path = case/'_work/review_clips'/name
                if not path.is_file() or item.get('audio_sha256') != file_sha256(path):
                    errors.append(f'回听文件缺失或变化：{name}')
                rows = matching_review_rows(sheet,item)
                if len(rows) != 1:
                    errors.append(f'终审表未唯一对应音频：{name}'); continue
                cells = [x.strip() for x in rows[0].strip('|').split('|')]
                tail = cells[-3:]
                if any(not x or re.search(r'待填|待定|未完成|待确认',x) for x in tail) or not re.search(r'\d{4}-\d{2}-\d{2}', tail[-1]):
                    errors.append(f'回听结论或真人签字未完成：{name}')
            if mode == 'solo':
                report=(case/'outputs/03_编辑质检报告.md').read_text(encoding='utf-8-sig')
                if not re.search(r'^- 是否可发送给学员：\s*(?:是|可发送)\s*$',report,re.M):
                    errors.append('质检报告尚未由真人确认可发送给学员')
                from audit_draft import audit_human_review_metrics
                errors.extend(f.message for f in audit_human_review_metrics(case,report) if f.level=='ERROR')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f'证据材料缺失或无效：{exc}')
    return mode, errors


def main() -> int:
    p=argparse.ArgumentParser(description='统一证据与编辑审计；普通通过不等于人工终审')
    p.add_argument('case',type=Path)
    group=p.add_mutually_exclusive_group()
    group.add_argument('--share',action='store_true');group.add_argument('--release',action='store_true')
    a=p.parse_args();case=a.case.resolve()
    mode,errors=shared_errors(case,handoff=a.share or a.release)
    if a.share and mode!='solo': errors.append('--share 仅用于单人学员稿')
    if a.release and mode!='dual': errors.append('--release 仅用于双人公众号稿')
    for e in errors:print('ERROR PACKAGE_GATE '+e)
    if mode not in {'solo','dual'}:
        print('AUDIT=FAIL');return 1
    script='audit_solo_share.py' if mode=='solo' else 'audit_draft.py'
    flags=['--share'] if a.share else ['--release'] if a.release else []
    result=subprocess.run([sys.executable,str(Path(__file__).parent/script),str(case),*flags])
    failed=bool(errors) or result.returncode != 0
    print('PACKAGE_AUDIT='+('FAIL' if failed else 'PASS'))
    return int(failed)

if __name__=='__main__':raise SystemExit(main())
