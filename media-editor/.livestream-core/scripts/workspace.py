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
from source_turns import CONTRACT,INDEX,index_for,contract_state

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def outside_install(path: Path) -> None:
    if path.resolve().is_relative_to(ROOT):
        raise ValueError('案例或运行环境必须放在 Skill 安装目录之外，避免升级丢失或误打包素材。')


def source_timing(text: str, *, allow_same_second: bool = False) -> tuple[list[int], list[str]]:
    seconds, errors = [], []
    for match in SPEAKER_HEADER_RE.finditer(text):
        parts = [int(x) for x in match.group('time').split(':')]
        # MM:SS may contain elapsed minutes above 59; HH:MM:SS may not.
        if parts[-1] >= 60 or (len(parts) == 3 and parts[-2] >= 60):
            errors.append(f"时间码超出合法范围：{match.group('time')}")
        seconds.append(sum(v * (60 ** i) for i, v in enumerate(reversed(parts))))
    if any(b < a or (b == a and not allow_same_second) for a, b in zip(seconds, seconds[1:])):
        errors.append('原稿时间码不严格递增，请保留原稿并核查转换结果')
    return seconds, errors


def init_case(case: Path, replay: Path, transcript: Path, people: Path | None = None,
              profile: Path | None = None, *, subtitle_source: str = 'captions', transcript_contract: str = 'strict') -> Path:
    case = case.expanduser().resolve()
    outside_install(case)
    if case.exists():
        raise FileExistsError(f'场次已经存在，保留原文件：{case}。继续旧场次请运行 doctor --case。')
    replay, transcript = replay.expanduser().resolve(), transcript.expanduser().resolve()
    people = people.expanduser().resolve() if people else None
    for p in [replay, transcript] + ([people] if people else []):
        if not p.is_file() or not p.stat().st_size:
            raise ValueError(f'输入不存在或为空：{p}')
    if transcript_contract not in {'strict','ordered-turns'}:raise ValueError('未知逐段原稿合同')
    if transcript_contract!='strict' and transcript.suffix.lower() in {'.srt','.vtt'}:raise ValueError('逐段原稿合同不改变SRT/VTT字幕合同')
    imported = None
    if transcript.suffix.lower() in {'.srt', '.vtt'}:
        raw_subtitles = transcript.read_bytes()
        subtitle_source_hash = hashlib.sha256(raw_subtitles).hexdigest()
        text, imported = convert_subtitles(raw_subtitles, transcript.suffix.lower()[1:], source_kind=subtitle_source)
    else:
        if subtitle_source != 'captions':
            raise ValueError('--subtitle-source 仅适用于 SRT/VTT，不能改变普通原稿的校验规则')
        text = transcript.read_text(encoding='utf-8-sig')
    if not SPEAKER_HEADER_RE.search(text):
        raise ValueError('可直接提供 SRT/VTT 字幕，或使用“发言人1   00:00”格式的转写稿。保留原文件，不从摘要扩写。')
    # The audited parser contract is deliberately explicit; avoid silently dropping other headers.
    if any(not m.group('label').startswith('发言人') for m in SPEAKER_HEADER_RE.finditer(text)):
        raise ValueError('请先将 Speaker/说话人 标签另存为 发言人1/发言人2 格式；不得覆盖原文件。')
    _, timing_errors = source_timing(text,allow_same_second=transcript_contract=='ordered-turns')
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
        provenance={'version':1,'files':records}
        if transcript_contract=='ordered-turns':
            write_json(stage/INDEX,index_for(target))
            provenance.update(transcript_contract=CONTRACT,turn_index_sha256=file_sha256(stage/INDEX))
        write_json(stage/'_work/输入来源.json',provenance)
        stage.rename(case)
    return case


def input_manifest_errors(case: Path) -> list[str]:
    manifest = case / '_work/输入来源.json'
    if not manifest.exists():
        if (case/INDEX).exists():return ['逐块索引缺少输入来源合同']
        if (case / '_work/字幕导入记录.json').exists():
            return ['字幕场次缺少输入来源记录，不能跳过原件和转换校验']
        return []  # Existing cases keep their original provenance scheme.
    errors = contract_state(case)[1]
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
        if max(cue['end_ms'] for cue in imported['cues']) / 1000 > length + 2:
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
        ordered,contract_errors=contract_state(case)
        seconds, timing_errors = source_timing(text,allow_same_second=ordered)
        if ordered and len(set(seconds))<len(seconds):warnings.append('同秒发言已保留独立块编号，原时间未改；边界和归属必须核对，不能按原标签直接确认。')
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
            imported = json.loads(subtitle_manifest.read_text(encoding='utf-8'))
            if imported.get('grouped_block_count'):
                warnings.append('同秒字幕已按原顺序逐行归入时间块，精确毫秒及逐条映射见字幕导入记录；同块不代表同一人，必须核对声音边界')
            if imported.get('timing_overlaps'):
                positions = '、'.join(dict.fromkeys(row['normalized_time'] for row in imported['timing_overlaps']))
                warnings.append(f'完整转写稿保留了时间重叠，未去重或修改时间；需回放核对内容和边界：{positions}。逐条记录见字幕导入记录')
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
                          'start_seconds':start, 'duration_seconds':seconds,
                          'audio_sha256':file_sha256(stage/name)})
        write_json(stage / 'manifest.json', {'source_sha256':file_sha256(replay),
                   'source_duration_seconds':length,
                   'unique_clip_count':len(windows), 'clips':items})
        write_json(stage / '核验记录.json', [{'position':item['position'],'start_seconds':item['start_seconds'],
                   'end_seconds':item['start_seconds']+item['duration_seconds'],'method':'待填写','finding':'填写实际发现'} for item in items])
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
    import html
    import posixpath
    import re
    from urllib.parse import quote, unquote, urlsplit, urlunsplit

    case = case.expanduser().resolve()
    source = case / 'outputs'
    destination = destination.expanduser()
    if source.is_symlink() or destination.is_symlink():
        raise ValueError('导出源或目标是符号链接，请先核对真实目录')
    destination = destination.resolve()
    if not source.is_dir():
        raise ValueError('缺少 outputs 目录')
    if destination.is_relative_to(case) or source.is_relative_to(destination):
        raise ValueError('阅读副本须位于本场案例之外，且不能包含源目录')
    files = list(source.rglob('*'))
    if any(p.is_symlink() for p in files):
        raise ValueError('导出中存在符号链接，请先核对内容')
    required = ['01_公众号可发布稿.md', '02_待核问题清单.md', '03_编辑质检报告.md']
    if any(not (source / n).is_file() for n in required):
        raise ValueError('三份固定交付尚未齐全')

    work = case / '_work'
    materials = '核对材料'
    document_names = ('待核原文索引.md', '回听辅助索引.md', '回听终审表.md')
    audio_suffixes = {'.m4a', '.mp3', '.wav', '.aac', '.flac', '.ogg', '.opus'}
    mapped, owners, payloads, pending = {}, {}, {}, []

    def checked(path: Path) -> Path:
        path = Path(os.path.normpath(path))
        if not path.is_relative_to(case):
            raise ValueError(f'阅读材料越出本场案例：{path.name}')
        relative = path.relative_to(case)
        if any((case / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
            raise ValueError(f'阅读材料经过符号链接：{relative.as_posix()}')
        if not path.is_file():
            raise FileNotFoundError(f'阅读材料缺失：{relative.as_posix()}')
        return path

    def include(path: Path) -> str:
        path = checked(path)
        if path in mapped:
            return mapped[path]
        if path.is_relative_to(source):
            relative = path.relative_to(source).as_posix()
        elif path.parent == work and path.name in document_names:
            relative = materials + '/' + path.name
        elif path.parent == work / 'review_clips' and path.suffix.lower() in audio_suffixes:
            relative = materials + '/review_clips/' + path.name
        else:
            raise ValueError(f'链接指向非阅读材料，不自动复制：{path.relative_to(case).as_posix()}')
        if relative in owners and owners[relative] != path:
            raise ValueError(f'阅读材料名称冲突，请使用独立副本目录：{relative}')
        mapped[path], owners[relative] = relative, path
        pending.append(path)
        return relative

    for path in files:
        if path.is_file():
            include(path)
    material_links = []
    for name in document_names:
        path = work / name
        if path.exists() or path.is_symlink():
            material_links.append((name.removesuffix('.md'), include(path)))

    manifest_path = work / 'review_clips/manifest.json'
    if manifest_path.exists() or manifest_path.is_symlink():
        manifest = json.loads(checked(manifest_path).read_text(encoding='utf-8-sig'))
        clips, samples = manifest.get('clips', []), manifest.get('samples', [])
        if not isinstance(clips, list) or not isinstance(samples, list):
            raise ValueError('回听材料清单格式错误')
        audio_lines = ['# 回听音频', '', '音频只辅助核对原话，不代表已完成人工终审。', '']
        seen_audio = set()
        for item in clips + samples:
            if not isinstance(item, dict):
                raise ValueError('回听材料清单格式错误')
            filename = item.get('filename', '')
            if not isinstance(filename, str) or not filename or '/' in filename or '\\' in filename or Path(filename).name != filename:
                raise ValueError('回听材料清单含无效文件名')
            audio = checked(work / 'review_clips' / filename)
            if item.get('audio_sha256') and file_sha256(audio) != item['audio_sha256']:
                raise ValueError(f'回听音频与清单不一致：{filename}')
            include(audio)
            if filename not in seen_audio:
                label = str(item.get('timecode', filename)).replace('\n', ' ')
                audio_lines.append(f'- {label}：[打开音频](review_clips/{quote(filename)})')
                seen_audio.add(filename)
        if seen_audio and not (work / '回听辅助索引.md').exists():
            relative = materials + '/回听音频.md'
            if relative in owners:
                raise ValueError('回听音频入口与已有文件同名')
            payloads[relative] = ('\n'.join(audio_lines) + '\n').encode('utf-8')
            material_links.append(('回听音频', relative))

    markdown_link = re.compile(r'(!?\[[^\]\r\n]*\]\()(<[^>\r\n]+>|[^\s)\r\n]+)((?:[ \t]+[\'\"][^\r\n]*[\'\"])?\))')
    reference_link = re.compile(r'^(\[[^\]\r\n]+\]:[ \t]*)(<[^>\r\n]+>|[^\s\r\n]+)', re.M)
    html_link = re.compile(r'(\b(?:src|href)\s*=\s*)([\'\"])([^\'\"]+)\2', re.I)

    def rewrite_url(raw: str, path: Path) -> str:
        wrapped = raw.startswith('<') and raw.endswith('>')
        value = raw[1:-1] if wrapped else raw
        parts = urlsplit(html.unescape(value))
        if parts.scheme or parts.netloc:
            if parts.scheme.lower() == 'file' or re.match(r'^[A-Za-z]:[\\/]', value):
                raise ValueError('阅读材料不能依赖本机绝对路径')
            return raw
        if not parts.path:
            return raw
        local = unquote(parts.path)
        if local.startswith('/') or '\\' in local:
            raise ValueError('阅读材料不能依赖本机绝对路径或反斜线路径')
        relative = include(path.parent / local)
        current_parent = posixpath.dirname(mapped[path]) or '.'
        replacement = posixpath.relpath(relative, current_parent)
        # Existing in-folder links need no change, preserving prose and formatting.
        if replacement == local:
            return raw
        value = urlunsplit(('', '', quote(replacement, safe='/'), parts.query, parts.fragment))
        return '<' + value + '>' if wrapped else value

    def rewrite_document(text: str, path: Path) -> str:
        output, fence = [], None
        for line in text.splitlines(keepends=True):
            marker = re.match(r'^[ \t]{0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
            if marker and fence is None:
                fence = marker.group(1)
                output.append(line)
                continue
            if fence is not None:
                output.append(line)
                if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) and not marker.group(2).strip():
                    fence = None
                continue
            # Link-looking text inside code examples is not a reading dependency.
            spans = []
            def hide_code(match):
                spans.append(match.group())
                return f'\x00CODE{len(spans) - 1}\x00'
            line = re.sub(r'(`+)(.+?)\1', hide_code, line)
            line = markdown_link.sub(lambda m: m.group(1) + rewrite_url(m.group(2), path) + m.group(3), line)
            line = reference_link.sub(lambda m: m.group(1) + rewrite_url(m.group(2), path), line)
            line = html_link.sub(lambda m: m.group(1) + m.group(2) + rewrite_url(m.group(3), path) + m.group(2), line)
            for index, value in enumerate(spans):
                line = line.replace(f'\x00CODE{index}\x00', value)
            output.append(line)
        return ''.join(output)

    while pending:
        path = pending.pop(0)
        data = path.read_bytes()
        if path.suffix.lower() in {'.md', '.html', '.htm'}:
            data = rewrite_document(data.decode('utf-8'), path).encode('utf-8')
        payloads[mapped[path]] = data

    entry = '00_从这里开始.md'
    if entry not in payloads:
        payloads[entry] = ('# 整理结果\n\n' + '\n'.join(f'- [{name}]({quote(name)})' for name in required) + '\n').encode('utf-8')
    if material_links:
        links = '\n\n## 核对材料\n\n' + '\n'.join(f'- [{label}]({quote(relative, safe="/")})' for label, relative in material_links) + '\n'
        payloads[entry] += links.encode('utf-8')

    if destination.exists():
        if any(path.is_symlink() for path in destination.rglob('*')):
            raise ValueError('阅读副本中存在符号链接，请先核对内容')
        if all((destination / name).is_file() and (destination / name).read_bytes() == data for name, data in payloads.items()):
            return destination
        raise FileExistsError('导出目标已有不同内容，可能含人工修改；请使用新目录，禁止自动覆盖。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        stage = Path(temp) / 'outputs'
        stage.mkdir()
        for name, data in payloads.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if target.read_bytes() != data:
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
    p.add_argument('--subtitle-source', choices=['captions', 'full-transcript'], default='captions',
                   help='SRT/VTT 来源；仅已知为完整逐段转写时选 full-transcript，原样保留重叠与重复条目供核对')
    p.add_argument('--transcript-contract',choices=['strict','ordered-turns'],default='strict',help='仅秒级逐段原稿；显式保留同秒发言，不重排、不改时间、不确认人物归属')
    p = sub.add_parser('templates'); p.add_argument('case', type=Path); p.add_argument('--mode', choices=['solo','dual'], required=True)
    p = sub.add_parser('probe'); p.add_argument('case', type=Path)
    p = sub.add_parser('export'); p.add_argument('case', type=Path); p.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'doctor':
            data = doctor(args.case); print(json.dumps(data, ensure_ascii=False, indent=2)); return 0 if data['status']=='READY' else 1
        if args.command == 'init': print(init_case(args.case,args.replay,args.transcript,args.people,args.profile,subtitle_source=args.subtitle_source,transcript_contract=args.transcript_contract))
        if args.command == 'templates': print(json.dumps(templates(args.case.resolve(),args.mode),ensure_ascii=False))
        if args.command == 'probe': print(probe(args.case.resolve()))
        if args.command == 'export': print(export_outputs(args.case,args.destination))
        return 0
    except Exception as exc:
        print(f'{args.command.upper()}=BLOCKED\n{exc}', file=sys.stderr); return 1


if __name__ == '__main__':
    raise SystemExit(main())
