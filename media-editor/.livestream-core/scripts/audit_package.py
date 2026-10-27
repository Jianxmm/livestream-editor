"""Shared provenance gate, followed by the independent solo/dual editorial audit."""
from __future__ import annotations
import argparse
from datetime import date
import json
from pathlib import Path
import re
import subprocess
import sys

from route_livestream import file_sha256, find_replay, route_position_errors
from personal_profile import validate_snapshot
from build_source_review_index import raw_timecode
from workspace import input_manifest_errors, subtitle_timing_errors
from source_turns import contract_state,blocks_from_text
from content_scope import read_content_scope
from review_validation import valid_review_date


def matching_review_rows(sheet: str, item: dict) -> list[str]:
    # A single audio window may support multiple distinct factual issues.
    # Each issue needs its own conclusion, even when the filename is shared.
    key=item['timecode'] if item.get('priority')=='SAMPLE' else str(item['issue_index'])
    return [line for line in sheet.splitlines() if line.startswith('|')
            and f"`{item['filename']}`" in line
            and line.strip('|').split('|')[0].strip()==key]


def signed_by_person(value: str) -> bool:
    match = re.search(r'\d{4}-\d{2}-\d{2}', value)
    if not match:
        return False
    if not valid_review_date(match.group()):
        return False
    name = (value[:match.start()] + value[match.end():]).strip(' /／|｜,，;；()（）[]【】')
    return bool(name) and name not in {'无', '未知', '不详'} and not re.search(r'待填|待定|待确认|未完成|未签', name)


def current_issues(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if not re.match(r'^\|\s*P[012]\s*\|', line):
            continue
        cells = [x.strip() for x in line.strip().strip('|').split('|')]
        if len(cells) != 6:
            raise ValueError('待核问题需保留六列表格')
        rows.append(dict(zip(('priority', 'timecode', 'issue_type', 'issue_text', 'action', 'status'), cells)))
    return rows


def issue_identity(item: dict) -> tuple:
    return (item.get('priority'), raw_timecode(str(item.get('timecode', ''))),
            item.get('issue_type'), item.get('issue_text'))


def issue_handoff_errors(case: Path, manifest: dict) -> list[str]:
    rows = current_issues((case/'outputs/02_待核问题清单.md').read_text(encoding='utf-8-sig'))
    identities = {issue_identity(row) for row in rows}
    clips = manifest.get('clips', [])
    errors = []
    for clip in clips:
        if issue_identity(clip) not in identities:
            errors.append('回听记录未对应当前待核问题，不能沿用旧问题的结论')
    draft = (case/'outputs/01_公众号可发布稿.md').read_text(encoding='utf-8-sig')
    for row in rows:
        if row['priority'] not in {'P0', 'P1'}:
            continue
        status = row['status']
        if status not in {'已回听', '已当事人确认', '已删除', '已保守表达'}:
            errors.append(f"{row['timecode']} 需要明确关闭方式，不能只写待确认或已解决")
        elif status == '已回听' and not any(issue_identity(c) == issue_identity(row) for c in clips):
            errors.append(f"{row['timecode']} 缺少这条问题对应的回听音频")
        elif status == '已保守表达':
            alternatives = re.findall(r'[“「『"]([^”」』"\n]+)[”」』"]', row['action'])
            if not alternatives or not any(value.strip() and value.strip() in draft for value in alternatives):
                errors.append(f"{row['timecode']} 请在处理建议中引用已采用的最终保守原句，并核对当前正文")
        if not row['action'] or re.fullmatch(r'待填|待定|待确认|已解决|已处理|已经处理好了|处理完成|无|—', row['action']):
            errors.append(f"{row['timecode']} 请记录实际核对依据或最终处理")
    return errors


def shared_errors(case: Path, *, handoff: bool = False) -> tuple[str, list[str]]:
    errors = validate_snapshot(case) + input_manifest_errors(case) + subtitle_timing_errors(case)
    full_path = case/'_work/完整编辑底稿.md'
    source_path = case/'inputs/02_带时间码原稿.md'
    if full_path.is_file() and source_path.is_file():
        errors.extend(read_content_scope(case, source_path.read_text(encoding='utf-8-sig'),
                                         full_path.read_text(encoding='utf-8-sig'), handoff=handoff).errors)
    mode = ''
    try:
        route = json.loads((case / '_work/场次分流.json').read_text(encoding='utf-8-sig'))
        errors.extend(route_position_errors(case,route,handoff=handoff))
        mode = route.get('mode', '')
        ordered,_=contract_state(case)
        if mode=='solo' and ordered and any(r['same_second'] for r in blocks_from_text(source_path.read_text(encoding='utf-8-sig'))):
            errors.append('单人原稿仍须严格递增；同秒逐段合同不能绕过单人审计')
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
            errors.extend(issue_handoff_errors(case, manifest))
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
                if len(tail) != 3 or any(not x or re.search(r'待填|待定|未完成|待确认|未回听|尚未回听|未核对',x) for x in tail[:2]):
                    errors.append(f'回听结论未完成：{name}')
                if not tail or not signed_by_person(tail[-1]):
                    errors.append(f'真人签字或有效日期未完成：{name}')
                if item.get('priority') != 'SAMPLE':
                    question = str(item.get('issue_type', '')) + '：' + str(item.get('issue_text', '')).replace('|', '／')
                    if len(cells) != 7 or raw_timecode(cells[1]) != raw_timecode(str(item.get('timecode', ''))) or cells[2] != question:
                        errors.append(f'终审表核验内容与当前问题不匹配：{name}')
            if mode == 'solo':
                report=(case/'outputs/03_编辑质检报告.md').read_text(encoding='utf-8-sig')
                if not re.search(r'^- 是否可发送给学员：\s*(?:是|可发送)\s*$',report,re.M):
                    errors.append('质检报告尚未由真人确认可发送给学员')
                from audit_draft import audit_human_review_metrics
                errors.extend(f.message for f in audit_human_review_metrics(case,report) if f.level=='ERROR')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
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
