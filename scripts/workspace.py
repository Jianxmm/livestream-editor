"""First-run and copy protection helpers. No network calls or inferred facts."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

from media_tools import duration, audio_clip
from route_livestream import file_sha256, find_replay, SPEAKER_HEADER_RE
from personal_profile import snapshot_profile, confirmed_rows, validate_snapshot
from subtitle_import import convert_subtitles

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def outside_install(path: Path) -> None:
    if path.resolve().is_relative_to(ROOT):
        raise ValueError('案例或运行环境必须放在 Skill 安装目录之外，避免升级丢失或误打包素材。')


def source_timing(text: str) -> tuple[list[int], list[str]]:
    seconds, errors = [], []
    for match in SPEAKER_HEADER_RE.finditer(text):
        parts = [int(x) for x in match.group('time').split(':')]
        # MM:SS may contain elapsed minutes above 59; HH:MM:SS may not.
        if parts[-1] >= 60 or (len(parts) == 3 and parts[-2] >= 60):
            errors.append(f"时间码超出合法范围：{match.group('time')}")
        seconds.append(sum(v * (60 ** i) for i, v in enumerate(reversed(parts))))
    if any(b <= a for a, b in zip(seconds, seconds[1:])):
        errors.append('原稿时间码不严格递增，请保留原稿并核查转换结果')
    return seconds, errors


def init_case(case: Path, replay: Path, transcript: Path, people: Path | None = None,
              profile: Path | None = None) -> Path:
    case = case.expanduser().resolve()
    outside_install(case)
    if case.exists():
        raise FileExistsError(f'场次已经存在，保留原文件：{case}。继续旧场次请运行 doctor --case。')
    replay, transcript = replay.expanduser().resolve(), transcript.expanduser().resolve()
    people = people.expanduser().resolve() if people else None
    for p in [replay, transcript] + ([people] if people else []):
        if not p.is_file() or not p.stat().st_size:
            raise ValueError(f'输入不存在或为空：{p}')
    imported = None
    if transcript.suffix.lower() in {'.srt', '.vtt'}:
        raw_subtitles = transcript.read_bytes()
        subtitle_source_hash = hashlib.sha256(raw_subtitles).hexdigest()
        text, imported = convert_subtitles(raw_subtitles, transcript.suffix.lower()[1:])
    else:
        text = transcript.read_text(encoding='utf-8-sig')
    if not SPEAKER_HEADER_RE.search(text):
        raise ValueError('可直接提供 SRT/VTT 字幕，或使用“发言人1   00:00”格式的转写稿。保留原文件，不从摘要扩写。')
    # The audited parser contract is deliberately explicit; avoid silently dropping other headers.
    if any(not m.group('label').startswith('发言人') for m in SPEAKER_HEADER_RE.finditer(text)):
        raise ValueError('请先将 Speaker/说话人 标签另存为 发言人1/发言人2 格式；不得覆盖原文件。')
    _, timing_errors = source_timing(text)
    if timing_errors:
        raise ValueError('；'.join(timing_errors))
    case.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.livestream-init-', dir=case.parent) as temp:
        stage = Path(temp) / case.name
        for d in ['inputs', 'outputs', '_work']:
            (stage / d).mkdir(parents=True, exist_ok=True)
        if profile:
            snapshot_profile(stage, profile)
        media = stage / 'inputs' / ('01_直播回放' + replay.suffix.lower())
        shutil.copyfile(replay, media)
        target = stage / 'inputs/02_带时间码原稿.md'
        # UTF-8 BOM is preserved; readers accept it. Source content is never rewritten.
        if imported is None:
            shutil.copyfile(transcript, target)
        else:
            target.write_text(text, encoding='utf-8')
            original = stage / ('inputs/05_平台原始导出' + transcript.suffix.lower())
            shutil.copyfile(transcript, original)
            if file_sha256(original) != subtitle_source_hash:
                raise RuntimeError('字幕在转换期间发生变化，未创建场次；请核对后重新导入。')
            imported['original'] = {'path': original.relative_to(stage).as_posix(), 'sha256': file_sha256(original)}
            imported['normalized'] = {'path': target.relative_to(stage).as_posix(), 'sha256': file_sha256(target)}
            write_json(stage / '_work/字幕导入记录.json', imported)
        people_target = stage / 'inputs/03_人物与专名表.md'
        if people:
            shutil.copyfile(people, people_target)
            if file_sha256(people) != file_sha256(people_target):
                raise RuntimeError('人物与专名表复制校验失败，原文件保留。')
        else:
            people_target.write_text('# 人物与专名表\n\n请根据回放或用户提供的资料填写真实姓名、角色及核验依据。\n\n| 规范写法 | 类别 | 必须替换的错写 | 可接受写法 | 状态 | 核验依据与说明 |\n| --- | --- | --- | --- | --- | --- |\n', encoding='utf-8')
            if profile:
                rows = confirmed_rows((stage / '_work/个人资料/01_专用词库.md').read_text(encoding='utf-8-sig'))
                with people_target.open('a', encoding='utf-8') as stream:
                    stream.write('\n'.join(rows) + ('\n' if rows else ''))
        records = []
        for src, dst in [(replay, media), (transcript, original if imported is not None else target)]:
            digest = file_sha256(src)
            if file_sha256(dst) != digest:
                raise RuntimeError(f'输入复制校验失败：{src.name}')
            records.append({'original_name': src.name, 'path': dst.relative_to(stage).as_posix(), 'sha256': digest})
        if imported is not None:
            for path in [target, stage / '_work/字幕导入记录.json']:
                records.append({'original_name': '生成的字幕副本或转换记录', 'path': path.relative_to(stage).as_posix(), 'sha256': file_sha256(path)})
        write_json(stage / '_work/输入来源.json', {'version': 1, 'files': records})
        stage.rename(case)
    return case


def input_manifest_errors(case: Path) -> list[str]:
    manifest = case / '_work/输入来源.json'
    if not manifest.exists():
        if (case / '_work/字幕导入记录.json').exists():
            return ['字幕场次缺少输入来源记录，不能跳过原件和转换校验']
        return []  # Existing cases keep their original provenance scheme.
    errors = []
    try:
        files = json.loads(manifest.read_text(encoding='utf-8'))['files']
        if not isinstance(files, list) or not files:
            raise ValueError('输入来源列表为空或格式错误')
        for item in files:
            path = (case / item['path']).resolve()
            if not path.is_relative_to(case.resolve()) or not path.is_file() or file_sha256(path) != item['sha256']:
                errors.append(f"初始化输入发生变化：{item['path']}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f'输入来源记录无法核对：{exc}')
    return errors


def subtitle_timing_errors(case: Path, length: float | None = None) -> list[str]:
    record = case / '_work/字幕导入记录.json'
    if not record.exists():
        return []
    try:
        imported = json.loads(record.read_text(encoding='utf-8'))
        if length is None:
            length = duration(find_replay(case))
        if imported['cues'][-1]['end_ms'] / 1000 > length + 2:
            return ['字幕结束时间超出回放时长，请核对是否为同一场或有剪辑偏移']
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as exc:
        return [f'字幕时间与回放无法核对：{exc}']
    return []


def inspect_case(case: Path) -> dict:
    errors, warnings = [], []
    try:
        replay = find_replay(case)
        length = duration(replay)
        text = (case / 'inputs/02_带时间码原稿.md').read_text(encoding='utf-8-sig')
        matches = list(SPEAKER_HEADER_RE.finditer(text))
        if not matches:
            errors.append('原稿没有可识别的发言人时间码')
        seconds, timing_errors = source_timing(text)
        errors.extend(timing_errors)
        if seconds and seconds[-1] > length + 2:
            errors.append('原稿时间码超出回放时长，可能不是同一场素材')
        if len(matches) < 6:
            warnings.append('原稿很短；仅适合输入演示，完整编辑规范需要足够的实际内容')
        if not (case / 'inputs/03_人物与专名表.md').is_file():
            errors.append('缺少人物与专名表')
        errors.extend(validate_snapshot(case))
        errors.extend(input_manifest_errors(case))
        subtitle_manifest = case / '_work/字幕导入记录.json'
        if subtitle_manifest.is_file():
            errors.extend(subtitle_timing_errors(case, length))
            warnings.append('字幕声音标签均未核验；“未标注”不是单人结论，需要对照回放')
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as exc:
        errors.append(str(exc))
    return {'errors': errors, 'warnings': warnings}


def doctor(case: Path | None = None) -> dict:
    checks = {'python_3_10_or_newer': sys.version_info >= (3, 10),
              'ffmpeg': bool(shutil.which('ffmpeg')), 'ffprobe': bool(shutil.which('ffprobe'))}
    data = {'status': 'READY' if all(checks.values()) else 'NEEDS_SETUP', 'checks': checks,
            'note': 'READY 仅表示基础工具存在，不证明 Agent 能听音频或已完成真人终审。双人模型由 setup-speakers 单独配置。'}
    if case:
        data['case'] = inspect_case(case.expanduser().resolve())
        if data['case']['errors']:
            data['status'] = 'BLOCKED'
    return data


def templates(case: Path, mode: str) -> list[str]:
    route = json.loads((case / '_work/场次分流.json').read_text(encoding='utf-8'))
    if route.get('status') != 'PASS' or route.get('mode') != mode:
        raise ValueError('先完成回放分流，再为匹配模式创建模板。')
    folder = ROOT / ('assets/solo' if mode == 'solo' else 'assets/templates')
    mapping = {'01_公众号可发布稿.md': 'outputs/01_公众号可发布稿.md',
               '02_待核问题清单.md': 'outputs/02_待核问题清单.md',
               '03_编辑质检报告.md': 'outputs/03_编辑质检报告.md',
               '04_内容覆盖表.md': '_work/内容覆盖表.md',
               '05_编辑决策台账.json': '_work/编辑决策台账.json',
               ('06_证据映射.json' if mode == 'solo' else '07_证据映射.json'): '_work/证据映射.json'}
    if mode == 'dual':
        mapping['08_说话人校准.json'] = '_work/说话人校准.json'
    created = []
    for source, target in mapping.items():
        dst = case / target
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            with dst.open('x', encoding='utf-8') as f:
                content=(folder / source).read_text(encoding='utf-8')
                if source=='08_说话人校准.json':
                    data=json.loads(content);replay=find_replay(case)
                    data['source_audio']=replay.relative_to(case).as_posix()
                    data['source_sha256']=file_sha256(replay)
                    labels={m.group('label').replace(' ', '') for m in SPEAKER_HEADER_RE.finditer(
                        (case/'inputs/02_带时间码原稿.md').read_text(encoding='utf-8-sig'))}
                    if '发言人未标注' in labels:
                        data['source_label_default_map']={label: None if label=='发言人未标注' else '待核标签映射' for label in sorted(labels)}
                    content=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
                f.write(content)
            created.append(target)
        except FileExistsError:
            pass  # Existing working/human files always win.
    return created


def probe(case: Path) -> Path:
    replay = find_replay(case)
    length = duration(replay)
    target = case / '_work/分流试听'
    if target.exists():
        raise FileExistsError(f'已有试听材料：{target}，先核对后复用。')
    case.joinpath('_work').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=case / '_work') as temp:
        stage = Path(temp) / '分流试听'
        stage.mkdir()
        items, windows = [], {}
        positions = ['开场','25%','50%','75%','结尾']
        for index, fraction in enumerate([0, .25, .5, .75, 1]):
            start = min(max(0, length - 20), length * fraction)
            seconds = min(20, length - start)
            key = (round(start,6), round(seconds,6))
            if key not in windows:
                name = f'{len(windows)+1:02d}.m4a'
                audio_clip(replay, stage / name, start, seconds)
                windows[key] = name
            name = windows[key]
            items.append({'position':positions[index], 'file':name,
                          'start_seconds':start, 'duration_seconds':seconds})
        write_json(stage / 'manifest.json', {'source_sha256':file_sha256(replay),
                   'unique_clip_count':len(windows), 'clips':items})
        lines = ['# 分流回听\n', '核对真实声音；短片仅辅助抽查，不能证明整场只有这些说话人。标签冲突处另外回听。\n']
        for name in windows.values():
            linked=[item for item in items if item['file']==name]
            labels='、'.join(item['position'] for item in linked)
            lines.append(f"- [{labels}，{linked[0]['start_seconds']:.1f} 秒]({name})：待核验\n")
        if len(windows)<5:
            lines.append('素材较短，部分检查位置共用同一音频；不能把重复片段当作五段独立抽听证据。\n')
        (stage / '回听.md').write_text('\n'.join(lines),encoding='utf-8')
        stage.rename(target)
    return target


def export_outputs(case: Path, destination: Path) -> Path:
    source = (case / 'outputs').resolve()
    destination = destination.expanduser().resolve()
    if not source.is_dir():
        raise ValueError('缺少 outputs 目录')
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('导出目录与源目录不能互相包含')
    files = list(source.rglob('*'))
    if any(p.is_symlink() for p in files):
        raise ValueError('导出中存在符号链接，请先核对内容')
    required = ['01_公众号可发布稿.md', '02_待核问题清单.md', '03_编辑质检报告.md']
    if any(not (source / n).is_file() for n in required):
        raise ValueError('三份固定交付尚未齐全')
    if destination.exists():
        if all((destination / p.relative_to(source)).is_file() and file_sha256(p) == file_sha256(destination / p.relative_to(source)) for p in files if p.is_file()):
            return destination
        raise FileExistsError('导出目标已有不同内容，可能含人工修改；请使用新目录，禁止自动覆盖。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        stage = Path(temp) / 'outputs'
        shutil.copytree(source, stage)
        for p in files:
            if p.is_file() and file_sha256(p) != file_sha256(stage / p.relative_to(source)):
                raise RuntimeError('导出校验失败')
        stage.rename(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description='直播整理环境与工作目录')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('doctor'); p.add_argument('--case', type=Path)
    p = sub.add_parser('init')
    for flag in ['case', 'replay', 'transcript']: p.add_argument('--'+flag, type=Path, required=True)
    p.add_argument('--people', type=Path)
    p.add_argument('--profile', type=Path, help='可选的个人词库、参考案例与偏好目录')
    p = sub.add_parser('templates'); p.add_argument('case', type=Path); p.add_argument('--mode', choices=['solo','dual'], required=True)
    p = sub.add_parser('probe'); p.add_argument('case', type=Path)
    p = sub.add_parser('export'); p.add_argument('case', type=Path); p.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'doctor':
            data = doctor(args.case); print(json.dumps(data, ensure_ascii=False, indent=2)); return 0 if data['status']=='READY' else 1
        if args.command == 'init': print(init_case(args.case,args.replay,args.transcript,args.people,args.profile))
        if args.command == 'templates': print(json.dumps(templates(args.case.resolve(),args.mode),ensure_ascii=False))
        if args.command == 'probe': print(probe(args.case.resolve()))
        if args.command == 'export': print(export_outputs(args.case,args.destination))
        return 0
    except Exception as exc:
        print(f'{args.command.upper()}=BLOCKED\n{exc}', file=sys.stderr); return 1


if __name__ == '__main__':
    raise SystemExit(main())
