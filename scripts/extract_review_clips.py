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
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from route_livestream import find_replay, file_sha256
from media_tools import audio_clip, duration


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
        (staging_dir / filename).replace(output_dir / filename)
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

    case_dir = args.case_dir.resolve()
    replay = find_replay(case_dir)
    issue_path = case_dir / "outputs/02_待核问题清单.md"
    source_path = case_dir / "inputs/02_带时间码原稿.md"
    output_dir = case_dir / "_work/review_clips"
    if not replay.is_file() or not issue_path.is_file() or not source_path.is_file():
        parser.error("任务目录缺少回放、原稿或待核问题清单")
    avconvert = shutil.which("ffmpeg")
    if not args.dry_run and not avconvert:
        parser.error("当前系统找不到 ffmpeg；请安装 FFmpeg 后重试")

    clips = share_duplicate_windows(
        parse_review_clips(
            issue_path.read_text(encoding="utf-8-sig"),
            padding=max(0, args.padding),
            max_duration=max(10, args.max_duration),
        )
    )
    samples = select_stratified_samples(source_path.read_text(encoding="utf-8-sig"), clips)
    planned = clips + samples
    if not planned:
        print("CLIPS=PASS files=0 unresolved_items=0")
        return 0
    if args.dry_run:
        print(json.dumps([asdict(clip) for clip in planned], ensure_ascii=False, indent=2))
        return 0

    length = duration(replay)
    if any(c.start >= length or any(to_seconds(t)>length for t in TIMESTAMP_RE.findall(c.timecode)) for c in planned):
        parser.error("回听窗口超出回放时长，请核对清单时间码及原稿是否匹配")
    clips=[replace(c,duration=min(c.duration,length-c.start)) for c in clips]
    samples=[replace(c,duration=min(c.duration,length-c.start)) for c in samples]
    planned=clips+samples
    (case_dir / "_work").mkdir(parents=True, exist_ok=True)
    review_sheet = case_dir / "_work" / REVIEW_SHEET_NAME
    desired_filenames = {clip.filename for clip in planned}
    if review_sheet.exists() and not args.refresh_review_sheet:
        previous_path=output_dir/'manifest.json'
        previous=json.loads(previous_path.read_text(encoding='utf-8-sig')) if previous_path.is_file() else {}
        if previous.get('source_sha256')!=file_sha256(replay) or previous.get('transcript_sha256')!=file_sha256(source_path):
            parser.error('回放或原稿变化，不能沿用旧回听结论；请先比较并归档旧终审表和片段。')
        previous_items=previous.get('clips',[])+previous.get('samples',[])
        fields=('priority','issue_index','timecode','issue_type','issue_text','start','duration','filename')
        if [{k:x.get(k) for k in fields} for x in previous_items]!=[asdict(c) for c in planned]:
            parser.error('回听问题或窗口变化，不能沿用旧结论；请先比较并归档旧终审表和片段。')
        existing_filenames = review_sheet_clip_filenames(
            review_sheet.read_text(encoding="utf-8-sig")
        )
        if existing_filenames != desired_filenames:
            parser.error(
                "待核项或抽听点已变化，但现有回听终审表不会被静默覆盖；"
                "确认表格尚未填写后使用 --refresh-review-sheet，已填写时请先归档旧表。"
            )
    manifest = {
        "version": 1,
        "source": replay.relative_to(case_dir).as_posix(),
        "issues": "outputs/02_待核问题清单.md",
        "source_sha256": file_sha256(replay),
        "transcript_sha256": file_sha256(source_path),
        "clips": [asdict(clip) for clip in clips],
        "samples": [asdict(sample) for sample in samples],
    }
    sheet_text = (
        render_review_sheet(clips, samples)
        if args.refresh_review_sheet or not review_sheet.exists()
        else None
    )
    staging_parent = case_dir / "_work"
    with tempfile.TemporaryDirectory(prefix=".review-clips-", dir=staging_parent) as directory:
        staging_dir = Path(directory)
        for clip in planned:
            destination = staging_dir / clip.filename
            if destination.exists():
                continue
            try:
                audio_clip(replay, destination, clip.start, clip.duration)
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or "").strip().splitlines()
                message = detail[-1] if detail else "FFmpeg 未返回错误详情"
                raise RuntimeError(
                    f"生成 {clip.filename} 失败；原终审包保持不变：{message}"
                ) from exc
        for item in manifest["clips"] + manifest["samples"]:
            item["audio_sha256"] = file_sha256(staging_dir / item["filename"])
        removed = install_staged_package(
            staging_dir,
            output_dir,
            planned,
            manifest,
            review_sheet_path=review_sheet,
            review_sheet_text=sheet_text,
        )
    print(
        f"CLIPS=PASS issue_files={len({clip.filename for clip in clips})} "
        f"issue_references={len(clips)} sample_files={len(samples)} "
        f"stale_removed={removed} destination={output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
