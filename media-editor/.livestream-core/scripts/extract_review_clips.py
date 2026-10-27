#!/usr/bin/env python3
"""Extract small audio clips for unresolved P0/P1 review items with FFmpeg."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from route_livestream import find_replay, file_sha256
from media_tools import audio_clip, duration
from build_source_review_index import raw_timecode


TIMESTAMP_RE = re.compile(r"(?<!\d)(?:\d{1,2}:)?\d{2}:\d{2}(?!\d)")
SOURCE_SPEAKER_TIMESTAMP_RE = re.compile(
    r"^发言人\s*[^\s]+\s+((?:\d{1,2}:)?\d{2}:\d{2})\s*$", re.MULTILINE
)
ISSUE_ROW_RE = re.compile(r"^\|\s*(P[01])\s*\|", re.MULTILINE)


def to_seconds(value: str) -> int:
    parts = [int(part) for part in value.split(":" )]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def format_seconds(value: int) -> str:
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}h{minutes:02d}m{seconds:02d}s"


def format_timecode(value: int) -> str:
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class ReviewClip:
    priority: str
    issue_index: int
    timecode: str
    issue_type: str
    issue_text: str
    start: int
    duration: int
    filename: str
    issue_id: str = ""


def issue_identity(item: dict[str, object]) -> tuple[str, str, str, str]:
    return (str(item.get('priority', '')), raw_timecode(str(item.get('timecode', ''))),
            str(item.get('issue_type', '')), str(item.get('issue_text', '')))


def issue_id(item: dict[str, object]) -> str:
    encoded = json.dumps(issue_identity(item), ensure_ascii=False).encode('utf-8')
    return 'I' + hashlib.sha256(encoded).hexdigest()[:20]


def review_issues(text: str) -> list[dict[str, object]]:
    rows, seen = [], set()
    for line in text.splitlines():
        if not ISSUE_ROW_RE.match(line):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) != 6:
            raise ValueError('待核问题需保留六列表格，不能截断人工填写的内容。')
        row = dict(zip(('priority', 'timecode', 'issue_type', 'issue_text', 'action', 'status'), cells))
        row['timecode'] = raw_timecode(row['timecode'])
        row['issue_id'] = issue_id(row)
        if row['issue_id'] in seen:
            raise ValueError('同一时间和内容的待核问题重复，无法唯一对应人工结论；请先核对重复行。')
        seen.add(row['issue_id'])
        rows.append(row)
    return rows


def stored_clip(item: dict[str, object]) -> ReviewClip:
    required = ('priority', 'issue_index', 'timecode', 'issue_type', 'issue_text', 'start', 'duration', 'filename')
    if not isinstance(item, dict) or any(key not in item for key in required):
        raise ValueError('旧回听清单缺少时间窗口或问题信息，不能自动迁移人工结论。')
    name = item['filename']
    if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name or name in {'', '.', '..'}:
        raise ValueError('回听清单包含越界音频文件名。')
    index, start, length = item['issue_index'], item['start'], item['duration']
    if not isinstance(index, int) or isinstance(index, bool) or index < 1:
        raise ValueError('旧回听问题编号无效。')
    if any(not isinstance(n, (int, float)) or isinstance(n, bool) or not 0 <= n < float('inf') for n in (start, length)) or length <= 0:
        raise ValueError('旧回听音频时间窗口无效。')
    values = {key: item[key] for key in required}
    values['timecode'] = raw_timecode(str(values['timecode']))
    values['issue_id'] = '' if item['priority'] == 'SAMPLE' else issue_id(item)
    return ReviewClip(**values)


def split_window(start: int, end: int, *, padding: int, max_duration: int) -> list[tuple[int, int]]:
    window_start = max(0, start - padding)
    window_end = max(window_start + 1, end + padding)
    windows: list[tuple[int, int]] = []
    cursor = window_start
    while cursor < window_end:
        duration = min(max_duration, window_end - cursor)
        windows.append((cursor, duration))
        if cursor + duration >= window_end:
            break
        cursor += max(1, max_duration - 2)
    return windows


def merge_overlapping_windows(
    windows: list[tuple[int, int]], *, max_duration: int
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, duration in sorted(windows):
        end = start + duration
        if merged:
            previous_start, previous_duration = merged[-1]
            previous_end = previous_start + previous_duration
            combined_end = max(previous_end, end)
            if start <= previous_end and combined_end - previous_start <= max_duration:
                merged[-1] = (previous_start, combined_end - previous_start)
                continue
        merged.append((start, duration))
    return merged


def parse_review_clips(text: str, *, padding: int = 8, max_duration: int = 90) -> list[ReviewClip]:
    clips: list[ReviewClip] = []
    issue_index = 0
    for line in text.splitlines():
        row = ISSUE_ROW_RE.match(line)
        if not row:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6 or cells[-1] != "待确认":
            continue
        issue_index += 1
        priority, time_cell, issue_type, issue_text = cells[:4]
        segments = re.split(r"[、,，]", time_cell)
        issue_windows: list[tuple[int, int]] = []
        for segment in segments:
            timestamps = TIMESTAMP_RE.findall(segment)
            if not timestamps:
                continue
            start = to_seconds(timestamps[0])
            end = to_seconds(timestamps[1]) if len(timestamps) > 1 else start + 30
            issue_windows.extend(
                split_window(start, end, padding=padding, max_duration=max_duration)
            )
        for window_index, (clip_start, duration) in enumerate(
            merge_overlapping_windows(issue_windows, max_duration=max_duration), start=1
        ):
            filename = (
                f"{priority}-{issue_index:02d}-{window_index:02d}-"
                f"{format_seconds(clip_start)}.m4a"
            )
            clips.append(
                ReviewClip(
                    priority=priority,
                    issue_index=issue_index,
                    timecode=time_cell,
                    issue_type=issue_type,
                    issue_text=issue_text,
                    start=clip_start,
                    duration=duration,
                    filename=filename,
                )
            )
    return clips


def share_duplicate_windows(clips: list[ReviewClip]) -> list[ReviewClip]:
    """Reuse one audio file when several checks point to the same time window."""
    filenames: dict[tuple[int, int], str] = {}
    shared: list[ReviewClip] = []
    for clip in clips:
        key = (clip.start, clip.duration)
        filename = filenames.setdefault(key, clip.filename)
        shared.append(clip if filename == clip.filename else replace(clip, filename=filename))
    return shared


def select_stratified_samples(
    source: str,
    excluded: list[ReviewClip],
    *,
    count: int = 5,
    duration: int = 45,
) -> list[ReviewClip]:
    source_seconds = [
        to_seconds(value) for value in SOURCE_SPEAKER_TIMESTAMP_RE.findall(source)
    ]
    if not source_seconds or count <= 0:
        return []
    interview_end = max(source_seconds)
    candidates = sorted({value for value in source_seconds if 120 <= value <= interview_end - 120})
    excluded_ranges = [
        (max(0, clip.start - 60), clip.start + clip.duration + 60) for clip in excluded
    ]
    eligible = [
        value
        for value in candidates
        if not any(start <= value <= end for start, end in excluded_ranges)
    ]
    if len(eligible) < count:
        eligible = [
            value
            for value in candidates
            if not any(clip.start <= value <= clip.start + clip.duration for clip in excluded)
        ]
    seed = int.from_bytes(hashlib.sha256(source.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    selected: list[int] = []
    for index in range(count):
        bin_start = interview_end * index / count
        bin_end = interview_end * (index + 1) / count
        bin_candidates = [
            value for value in eligible if bin_start <= value < bin_end and value not in selected
        ]
        if bin_candidates:
            selected.append(rng.choice(bin_candidates))
    if len(selected) < count:
        remaining = [value for value in eligible if value not in selected]
        rng.shuffle(remaining)
        selected.extend(remaining[: count - len(selected)])
    samples: list[ReviewClip] = []
    for index, second in enumerate(sorted(selected), start=1):
        start = max(0, second - 8)
        samples.append(
            ReviewClip(
                priority="SAMPLE",
                issue_index=index,
                timecode=format_timecode(second),
                issue_type="随机抽听",
                issue_text="检查说话人、遗漏、过度改写和人物语气",
                start=start,
                duration=duration,
                filename=f"SAMPLE-{index:02d}-{format_seconds(start)}.m4a",
            )
        )
    return samples


def render_review_sheet(clips: list[ReviewClip], samples: list[ReviewClip] | None = None) -> str:
    samples = samples or []
    grouped: dict[int, list[ReviewClip]] = {}
    for clip in clips:
        grouped.setdefault(clip.issue_index, []).append(clip)
    lines = [
        "# 回听终审表",
        "",
        "按顺序播放短音频并填写原话与最终处理。未填写的项目不得标记为已回听。",
        "机器辅助转写如存在，仅用于定位疑点，不能替代真人回听或关闭 P1。",
        "",
        "| 序号 | 时间码 | 核验内容 | 音频文件 | 听到的原话 | 最终处理 | 终审人/日期 |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for issue_index, issue_clips in sorted(grouped.items()):
        first = issue_clips[0]
        files = "<br>".join(f"`{clip.filename}`" for clip in issue_clips)
        issue_text = first.issue_text.replace("|", "／")
        lines.append(
            f"| {issue_index} | {first.timecode} | {first.issue_type}：{issue_text} | "
            f"{files} | 待填 | 待定 | 待填 |"
        )
    lines.extend(
        [
            "",
            "## 随机抽听",
            "",
            "P1关闭后，从未列入P1的时间段随机抽听至少5段，检查说话人、遗漏、过度改写和人物语气。",
            "",
            "| 时间码 | 音频文件 | 抽听结论 | 是否修订 | 终审人/日期 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for sample in samples:
        lines.append(
            f"| {sample.timecode} | `{sample.filename}` | 待填 | 待填 | 待填 |"
        )
    lines.append("")
    return "\n".join(lines)


def managed_clip_filenames(manifest_path: Path) -> set[str]:
    if not manifest_path.is_file():
        return set()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return set()
    items = list(data.get("clips", [])) + list(data.get("samples", []))
    return {
        str(item["filename"])
        for item in items
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    }


def remove_stale_managed_clips(output_dir: Path, desired: set[str]) -> int:
    """Remove only clip files recorded in the previous generated manifest."""
    removed = 0
    for filename in managed_clip_filenames(output_dir / "manifest.json") - desired:
        candidate = output_dir / filename
        if candidate.parent == output_dir and candidate.is_file():
            candidate.unlink()
            removed += 1
    return removed


def review_sheet_clip_filenames(text: str) -> set[str]:
    return set(re.findall(r"`([^`]+\.m4a)`", text))


def install_staged_package(
    staging_dir: Path,
    output_dir: Path,
    planned: list[ReviewClip],
    manifest: dict[str, object],
    *,
    review_sheet_path: Path,
    review_sheet_text: str | None,
) -> int:
    """Install only after every staged clip exists and is non-empty."""
    desired = {clip.filename for clip in planned}
    invalid = [
        clip.filename
        for clip in planned
        if not (staging_dir / clip.filename).is_file()
        or (staging_dir / clip.filename).stat().st_size == 0
    ]
    if invalid:
        raise RuntimeError(f"回听音频尚未完整生成，保留原终审包不变：{invalid[:3]}")

    output_dir.mkdir(parents=True, exist_ok=True)
    previous = managed_clip_filenames(output_dir / "manifest.json")
    for filename in sorted(desired):
        target = output_dir / filename
        if target.is_symlink():
            raise ValueError('回听目标音频是符号链接，停止写入。')
        if target.is_file() and file_sha256(target) == file_sha256(staging_dir / filename):
            continue  # Preserve unchanged evidence bytes and timestamps.
        (staging_dir / filename).replace(target)
    removed = 0
    for filename in previous - desired:
        candidate = output_dir / filename
        if candidate.parent == output_dir and candidate.is_file():
            candidate.unlink()
            removed += 1

    if review_sheet_text is not None:
        staged_sheet = staging_dir / REVIEW_SHEET_NAME
        staged_sheet.write_text(review_sheet_text, encoding="utf-8")
        staged_sheet.replace(review_sheet_path)
    staged_manifest = staging_dir / "manifest.json"
    staged_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    staged_manifest.replace(output_dir / "manifest.json")
    return removed


REVIEW_SHEET_NAME = "回听终审表.md"


def plan_incremental_clips(text: str, previous: dict, *, padding: int = 8,
                           max_duration: int = 90) -> tuple[list[ReviewClip], list[dict]]:
    """Preserve issue numbers independently of row order and pending/closed state."""
    rows = review_issues(text)
    registry, indexes, groups = {}, {}, {}
    for record in previous.get('issue_registry', []):
        if not isinstance(record, dict) or record.get('issue_id') != issue_id(record):
            raise ValueError('旧问题登记表身份不完整，不能自动沿用人工结论。')
        if record['issue_id'] in registry:
            raise ValueError('旧问题登记表存在重复身份，不能自动迁移人工结论。')
        registry[record['issue_id']] = dict(record, active=False)
    for raw in previous.get('clips', []):
        clip = stored_clip(raw)
        groups.setdefault(clip.issue_id, []).append(clip)
        registry.setdefault(clip.issue_id, dict(zip(
            ('priority', 'timecode', 'issue_type', 'issue_text'), issue_identity(raw)),
            issue_id=clip.issue_id, issue_index=clip.issue_index, active=False))
    for key, record in registry.items():
        index = record.get('issue_index')
        if not isinstance(index, int) or isinstance(index, bool) or index < 1 or (index in indexes and indexes[index] != key):
            raise ValueError('旧问题编号不唯一，不能把人工结论移给另一个问题。')
        indexes[index] = key
        if any(clip.issue_index != index for clip in groups.get(key, [])):
            raise ValueError('旧回听清单的问题编号与登记表不一致。')
    next_index, clips = max(indexes, default=0) + 1, []
    for row in rows:
        key = row['issue_id']
        if key not in registry:
            registry[key] = {k: row[k] for k in ('priority', 'timecode', 'issue_type', 'issue_text', 'issue_id')}
            registry[key]['issue_index'] = next_index
            next_index += 1
        registry[key].update(active=True, status=row['status'])
        index = registry[key]['issue_index']
        if key in groups:
            clips.extend(groups[key])
            continue
        if row['status'] == '已回听':
            raise ValueError(f"{row['timecode']} 标为已回听，但没有本问题对应的旧音频和结论；请按实际情况重新核验，不能沿用另一问题的结论。")
        if row['status'] != '待确认':
            continue
        line = '| ' + ' | '.join(str(row[k]) for k in ('priority', 'timecode', 'issue_type', 'issue_text', 'action', 'status')) + ' |'
        parsed = parse_review_clips(line, padding=padding, max_duration=max_duration)
        if not parsed:
            raise ValueError(f"{row['timecode']} 缺少可定位的回听时间码。")
        for number, clip in enumerate(parsed, 1):
            clips.append(replace(clip, issue_index=index, issue_id=key,
                                 filename=f'{clip.priority}-{index:02d}-{number:02d}-{format_seconds(clip.start)}.m4a'))
    return share_duplicate_windows(sorted(clips, key=lambda c: (c.issue_index, c.start))), sorted(registry.values(), key=lambda r: r['issue_index'])


def stable_samples(source: str, clips: list[ReviewClip], previous: dict) -> list[ReviewClip]:
    old = [stored_clip(item) for item in previous.get('samples', [])]
    # New facts may now occupy an old random sample. Only those samples lose their independent status.
    kept = [sample for sample in old if not any(
        sample.start < clip.start + clip.duration and clip.start < sample.start + sample.duration for clip in clips)]
    if not old:
        return select_stratified_samples(source, clips)
    if len(kept) >= 5:
        return kept
    candidates = select_stratified_samples(source, clips + kept)
    used_indexes = {sample.issue_index for sample in kept}
    for candidate in candidates:
        if len(kept) >= 5:
            break
        if any(candidate.start < sample.start + sample.duration and sample.start < candidate.start + candidate.duration for sample in kept):
            continue
        index = next(n for n in range(1, len(kept) + 2) if n not in used_indexes)
        used_indexes.add(index)
        kept.append(replace(candidate, issue_index=index, filename=f'SAMPLE-{index:02d}-{format_seconds(candidate.start)}.m4a'))
    return sorted(kept, key=lambda sample: sample.issue_index)


def sheet_rows(text: str, clips: list[ReviewClip], samples: list[ReviewClip]) -> tuple[dict, set[int]]:
    """Identify managed rows only when their metadata still describes the previous package."""
    lines, result, managed = text.splitlines(), {}, set()
    groups = {}
    for clip in clips:
        groups.setdefault(clip.issue_index, []).append(clip)
    expected = {('issue', index): items for index, items in groups.items()}
    expected.update({('sample', sample.filename): [sample] for sample in samples})
    for key, items in expected.items():
        first = items[0]
        filenames = {item.filename for item in items}
        matches = []
        for number, line in enumerate(lines):
            if not line.startswith('|'):
                continue
            cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
            if key[0] == 'issue':
                found = len(cells) == 7 and cells[0] == str(first.issue_index)
                valid = found and raw_timecode(cells[1]) == first.timecode and cells[2] == first.issue_type + '：' + first.issue_text.replace('|', '／')
            else:
                found = len(cells) == 5 and cells[0] == first.timecode and first.filename in review_sheet_clip_filenames(line)
                valid = found
            if found:
                if not valid or review_sheet_clip_filenames(line) != filenames:
                    raise ValueError('终审表的核验内容或音频被人工改动，无法安全合并；原文件保留，请先核对。')
                matches.append((number, line))
        if len(matches) != 1:
            raise ValueError('终审表未唯一对应旧问题或音频，无法安全合并；原文件保留。')
        result[key] = matches[0]
        managed.add(matches[0][0])
    for number, line in enumerate(lines):
        if review_sheet_clip_filenames(line) and number not in managed:
            raise ValueError('终审表含有无法对应旧清单的音频行，不能自动覆盖。')
    return result, managed


def merge_review_sheet(text: str, old_clips: list[ReviewClip], old_samples: list[ReviewClip],
                       clips: list[ReviewClip], samples: list[ReviewClip]) -> str:
    old_rows, managed = sheet_rows(text, old_clips, old_samples)
    fresh_rows, _ = sheet_rows(render_review_sheet(clips, samples), clips, samples)
    lines, replacements, new_issues, new_samples = text.splitlines(), {}, [], []
    for key, (_, new_line) in fresh_rows.items():
        if key in old_rows:
            position, old_line = old_rows[key]
            if key[0] == 'issue':
                # Link anchors in a timecode can move when source-index renumbers rows.
                # Keep all human answer cells verbatim; normalize only the time metadata.
                cells = old_line.split('|')
                plain = next(c for c in clips if c.issue_index == key[1]).timecode
                if cells[2].strip() != plain:
                    cells[2] = ' ' + plain + ' '
                    old_line = '|'.join(cells)
            replacements[position] = old_line
        elif key[0] == 'issue':
            new_issues.append(new_line)
        else:
            new_samples.append(new_line)
    issue_markers = [i for i, line in enumerate(lines) if line.strip() == '## 随机抽听']
    if new_issues and len(issue_markers) != 1:
        raise ValueError('终审表的段落结构已改变，无法安全追加问题；请保留原表后核对。')
    sample_rows = [number for key, (number, _) in old_rows.items() if key[0] == 'sample']
    sample_headers = [i for i, line in enumerate(lines) if line.startswith('| 时间码 | 音频文件 |')]
    sample_insertion = max(sample_rows) + 1 if sample_rows else sample_headers[0] + 2 if len(sample_headers) == 1 else None
    if new_samples and sample_insertion is None:
        raise ValueError('终审表的随机抽听表结构已改变，不能自动覆盖。')
    output = []
    for number in range(len(lines) + 1):
        if new_issues and number == issue_markers[0]:
            output.extend(new_issues + [''])
        if new_samples and number == sample_insertion:
            output.extend(new_samples)
        if number < len(lines):
            if number in replacements:
                output.append(replacements[number])
            elif number not in managed:
                output.append(lines[number])
    return '\n'.join(output) + ('\n' if text.endswith('\n') else '')


def archive_previous_package(output_dir: Path, sheet: Path) -> Path | None:
    manifest_path = output_dir / 'manifest.json'
    if not manifest_path.exists():
        return None
    payload = manifest_path.read_bytes() + (sheet.read_bytes() if sheet.exists() else b'')
    history = output_dir / 'history'
    if history.is_symlink():
        raise ValueError('回听历史目录是符号链接，停止写入以保护原文件。')
    history.mkdir(exist_ok=True)
    target = history / hashlib.sha256(payload).hexdigest()[:24]
    names = managed_clip_filenames(manifest_path) | {'manifest.json'}
    if (output_dir / 'machine_transcripts.json').exists():
        names.add('machine_transcripts.json')
    with tempfile.TemporaryDirectory(prefix='.archive-', dir=history) as directory:
        stage = Path(directory) / 'snapshot'; stage.mkdir()
        for name in names:
            source = output_dir / name
            if Path(name).name != name or '\\' in name or source.is_symlink():
                raise ValueError('旧回听清单含越界文件或链接，停止归档。')
            if source.is_file():
                shutil.copy2(source, stage / name)
        if sheet.exists():
            shutil.copy2(sheet, stage / REVIEW_SHEET_NAME)
        if target.exists():
            if any(not (target/p.name).is_file() or file_sha256(target/p.name) != file_sha256(p) for p in stage.iterdir()):
                raise ValueError('已有回听历史与待归档内容不同，保留现场。')
        else:
            stage.rename(target)
    return target


def extract_review_package(case_dir: Path, *, padding: int = 8, max_duration: int = 90,
                           refresh_review_sheet: bool = False, dry_run: bool = False) -> dict:
    if not isinstance(padding, int) or padding < 0 or not isinstance(max_duration, int) or max_duration < 10:
        raise ValueError('回听补充秒数需为非负整数，单片段上限需为至少10秒的整数。')
    case_dir = case_dir.expanduser().resolve()
    replay = find_replay(case_dir)
    source_path = case_dir / 'inputs/02_带时间码原稿.md'
    issue_path = case_dir / 'outputs/02_待核问题清单.md'
    output_dir = case_dir / '_work/review_clips'
    review_sheet = case_dir / '_work' / REVIEW_SHEET_NAME
    if (case_dir/'_work').is_symlink() or output_dir.is_symlink() or review_sheet.is_symlink():
        raise ValueError('回听目录或终审表经过符号链接，停止写入以保护人工文件。')
    previous_path = output_dir / 'manifest.json'
    if previous_path.is_symlink():
        raise ValueError('回听清单是符号链接，停止写入。')
    previous = json.loads(previous_path.read_text(encoding='utf-8-sig')) if previous_path.exists() else {}
    if not isinstance(previous, dict):
        raise ValueError('旧回听清单格式无效，保留原终审包。')
    replay_hash, transcript_hash = file_sha256(replay), file_sha256(source_path)
    if previous and (previous.get('source_sha256') != replay_hash or previous.get('transcript_sha256') != transcript_hash):
        raise ValueError('回放或原稿变化，不能沿用旧回听结论；请先比较并归档旧终审包。')
    old_clips = [stored_clip(item) for item in previous.get('clips', [])]
    old_samples = [stored_clip(item) for item in previous.get('samples', [])]
    old_sheet = review_sheet.read_text(encoding='utf-8-sig') if review_sheet.exists() else None
    if old_sheet is not None and not previous:
        raise ValueError('已有终审表但缺少对应旧清单，不能自动覆盖或迁移人工结论。')
    if previous and old_sheet is None:
        raise ValueError('旧回听清单存在但终审表缺失，无法确认人工结论；请先核对并恢复原表。')
    if old_sheet is not None:
        old_rows, _ = sheet_rows(old_sheet, old_clips, old_samples)
        if refresh_review_sheet:
            for key, (_, line) in old_rows.items():
                cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
                tail = cells[-3:]
                if any(cell not in {'待填', '待定', '待确认', '未完成', ''} for cell in tail):
                    raise ValueError('终审表已有人工结论，不能用刷新参数清空；请按增量续作保留结论。')
    prior_windows = {}
    for item in previous.get('clips', []) + previous.get('samples', []):
        clip = stored_clip(item); path = output_dir / clip.filename
        window = (clip.start, clip.duration, item.get('audio_sha256'))
        if clip.filename in prior_windows and prior_windows[clip.filename] != window:
            raise ValueError('同一旧音频对应不同窗口，不能沿用人工核验结论。')
        prior_windows[clip.filename] = window
        if path.is_symlink() or not path.is_file() or not item.get('audio_sha256') or file_sha256(path) != item['audio_sha256']:
            raise ValueError(f'旧回听音频缺失或变化，不能静默替换后沿用人工结论：{clip.filename}')
    clips, registry = plan_incremental_clips(issue_path.read_text(encoding='utf-8-sig'), previous,
                                            padding=padding, max_duration=max_duration)
    samples = stable_samples(source_path.read_text(encoding='utf-8-sig'), clips, previous)
    if dry_run:
        return {'planned': [asdict(c) for c in clips + samples]}
    length = duration(replay)
    planned = clips + samples
    if any(c.start >= length or any(to_seconds(t) > length for t in TIMESTAMP_RE.findall(c.timecode)) for c in planned):
        raise ValueError('回听窗口超出回放时长，请核对清单时间码及原稿是否匹配。')
    old_by_name = {item['filename']: item for item in previous.get('clips', []) + previous.get('samples', [])}
    def bounded(clip):
        old = old_by_name.get(clip.filename)
        if old:
            if old['start'] != clip.start or old['duration'] != clip.duration:
                raise ValueError('旧音频窗口发生变化，不能替换音频后沿用旧签字；请核对失效范围。')
            return clip
        return replace(clip, duration=min(clip.duration, length-clip.start))
    clips = [bounded(c) for c in clips]
    samples = [bounded(c) for c in samples]
    planned = clips + samples
    sheet_text = render_review_sheet(clips, samples) if old_sheet is None else merge_review_sheet(old_sheet, old_clips, old_samples, clips, samples)
    manifest = {'version': 1, 'review_revision': 2, 'source': replay.relative_to(case_dir).as_posix(),
                'issues': 'outputs/02_待核问题清单.md', 'source_sha256': replay_hash,
                'transcript_sha256': transcript_hash, 'issue_registry': registry,
                'clips': [asdict(c) for c in clips], 'samples': [asdict(c) for c in samples]}
    (case_dir / '_work').mkdir(parents=True, exist_ok=True)
    for clip in planned:
        target = output_dir / clip.filename
        if (target.exists() or target.is_symlink()) and clip.filename not in old_by_name:
            raise ValueError(f'新音频文件名已被其他文件占用，保留人工文件，不自动覆盖：{clip.filename}')
    with tempfile.TemporaryDirectory(prefix='.review-clips-', dir=case_dir/'_work') as directory:
        staging = Path(directory)
        for clip in planned:
            target = staging / clip.filename
            if target.exists():
                continue
            old = old_by_name.get(clip.filename)
            if old and old['start'] == clip.start and old['duration'] == clip.duration:
                shutil.copy2(output_dir/clip.filename, target)
            else:
                audio_clip(replay, target, clip.start, clip.duration)
        for item in manifest['clips'] + manifest['samples']:
            item['audio_sha256'] = file_sha256(staging/item['filename'])
        changed = previous != manifest or (old_sheet is not None and old_sheet != sheet_text)
        if changed and previous:
            archive_previous_package(output_dir, review_sheet)
        removed = install_staged_package(staging, output_dir, planned, manifest,
            review_sheet_path=review_sheet, review_sheet_text=sheet_text if old_sheet != sheet_text else None)
        if changed and (output_dir/'machine_transcripts.json').is_file():
            # Old derived text remains in the archived package, never masquerades as current.
            (output_dir/'machine_transcripts.json').unlink()
    return {'issue_files': len({c.filename for c in clips}), 'issue_references': len(clips),
            'sample_files': len(samples), 'stale_removed': removed, 'destination': str(output_dir)}


def main() -> int:
    parser = argparse.ArgumentParser(description="按待核清单时间码提取短音频，避免人工拖动整场回放。")
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--padding", type=int, default=8, help="疑点前后补充秒数，默认8秒")
    parser.add_argument("--max-duration", type=int, default=90, help="单片段最长秒数，默认90秒")
    parser.add_argument("--dry-run", action="store_true", help="只输出计划，不转换媒体")
    parser.add_argument(
        "--refresh-review-sheet",
        action="store_true",
        help="重建回听终审表；仅在尚未填写人工结论时使用",
    )
    args = parser.parse_args()

    if not args.dry_run and not shutil.which("ffmpeg"):
        parser.error("当前系统找不到 ffmpeg；请安装 FFmpeg 后重试")
    try:
        result = extract_review_package(args.case_dir, padding=max(0, args.padding),
            max_duration=max(10, args.max_duration), refresh_review_sheet=args.refresh_review_sheet, dry_run=args.dry_run)
        if args.dry_run:
            print(json.dumps(result['planned'], ensure_ascii=False, indent=2))
        else:
            print('CLIPS=PASS ' + ' '.join(f'{key}={value}' for key, value in result.items()))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.CalledProcessError) as exc:
        print('CLIPS=BLOCKED\n' + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
