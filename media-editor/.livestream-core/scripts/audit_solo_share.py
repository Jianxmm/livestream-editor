#!/usr/bin/env python3
"""Audit a source-faithful, single-speaker livestream editing package."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from speaker_analysis import verified_solo_route
from review_validation import valid_review_date
from content_scope import read_content_scope


REQUIRED_FILES = (
    "outputs/01_公众号可发布稿.md",
    "outputs/02_待核问题清单.md",
    "outputs/03_编辑质检报告.md",
    "_work/完整编辑底稿.md",
    "_work/内容覆盖表.md",
    "_work/证据映射.json",
    "_work/编辑决策台账.json",
)
SOURCE = "inputs/02_带时间码原稿.md"
PROPER_NOUNS = "inputs/03_人物与专名表.md"
DRAFT = "outputs/01_公众号可发布稿.md"
ISSUES = "outputs/02_待核问题清单.md"
REPORT = "outputs/03_编辑质检报告.md"
FULL_DRAFT = "_work/完整编辑底稿.md"
COVERAGE = "_work/内容覆盖表.md"
EVIDENCE = "_work/证据映射.json"
DECISIONS = "_work/编辑决策台账.json"
ROUTE = "_work/场次分流.json"
FULL_AUDIO = "outputs/04_整场音频.m4a"
AUDIO_MANIFEST = "_work/整场音频.json"
BODY_START = "<!-- SOLO_BODY_START -->"
BODY_END = "<!-- SOLO_BODY_END -->"
SOURCE_HEADER_RE = re.compile(
    r"^发言人\s*([^\s]+)\s+((?:\d{1,2}:)?\d{2}:\d{2})\s*$", re.MULTILINE
)
ISSUE_ROW_RE = re.compile(r"^\|\s*(P[012])\s*\|", re.MULTILINE)
TITLE_NUMBER_RE = re.compile(r"[0-9一二三四五六七八九十百千万两]")
ALLOWED_STATUSES = {"待确认", "已回听", "已当事人确认", "已删除", "已保守表达", "已解决"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def to_seconds(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"无效时间码：{value}")


def compact_chars(text: str) -> str:
    return re.sub(r"\s+", "", text)


def source_blocks(text: str) -> list[tuple[str, str, str]]:
    matches = list(SOURCE_HEADER_RE.finditer(text))
    blocks: list[tuple[str, str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((match.group(1), match.group(2), text[match.end() : end].strip()))
    return blocks


def body_between_markers(text: str) -> str | None:
    if BODY_START not in text or BODY_END not in text:
        return None
    start = text.index(BODY_START) + len(BODY_START)
    end = text.index(BODY_END, start)
    lines: list[str] = []
    for line in text[start:end].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ">", "---", "<!--")):
            continue
        stripped = re.sub(r"^\*\*[^*：]{1,30}：\*\*\s*", "", stripped)
        lines.append(stripped)
    return "\n".join(lines)


def parse_issue_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not ISSUE_ROW_RE.match(line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 6:
            rows.append(
                {
                    "priority": cells[0],
                    "timecode": cells[1],
                    "type": cells[2],
                    "text": cells[3],
                    "action": cells[4],
                    "status": cells[5],
                }
            )
    return rows


def markdown_field(text: str, name: str) -> str | None:
    match = re.search(rf"^-\s*{re.escape(name)}[：:]\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


@dataclass
class Audit:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)

    def error(self, code: str, message: str) -> None:
        self.errors.append(f"{code} {message}")

    def warning(self, code: str, message: str) -> None:
        self.warnings.append(f"{code} {message}")


def audit_case(case_dir: Path, *, share: bool = False, release: bool = False) -> Audit:
    case_dir = case_dir.resolve()
    result = Audit()
    handoff = share or release

    for relative in (SOURCE, PROPER_NOUNS, *REQUIRED_FILES):
        if not (case_dir / relative).is_file():
            result.error("MISSING_REQUIRED_FILE", relative)
    if result.errors:
        return result

    routed_solo = False
    route: dict[str, object] = {}
    route_path = case_dir / ROUTE
    if route_path.is_file():
        try:
            loaded_route = json.loads(route_path.read_text(encoding="utf-8-sig"))
            if not isinstance(loaded_route, dict):
                raise ValueError("场次分流JSON顶层必须为对象。")
            route = loaded_route
        except (json.JSONDecodeError, ValueError) as exc:
            result.error("INVALID_ROUTE_JSON", str(exc))
            route = {}
        if route.get("status") != "PASS" or route.get("mode") != "solo":
            result.error(
                "SOLO_ROUTE_REQUIRED",
                f"场次分流必须为 status=PASS、mode=solo，当前={route.get('status')}/{route.get('mode')}",
            )
        else:
            routed_solo = True
            if route.get("target_skill") != "references/solo.md":
                result.error(
                    "SOLO_ROUTE_TARGET",
                    f"场次分流目标应为 references/solo.md，当前={route.get('target_skill')}",
                )
            route_source = route.get("source", {})
            route_replay = route.get("replay", {})
            if not isinstance(route_source, dict) or not isinstance(route_replay, dict):
                result.error("SOLO_ROUTE_EVIDENCE", "场次分流缺少source或replay证据对象。")
            else:
                if route_source.get("path") != SOURCE:
                    result.error(
                        "SOLO_ROUTE_SOURCE_PATH",
                        f"场次分流原稿路径应为 {SOURCE}。",
                    )
                elif route_source.get("sha256") != file_sha256(case_dir / SOURCE):
                    result.error(
                        "SOLO_ROUTE_SOURCE_HASH",
                        "当前原稿与场次分流时的SHA-256不一致。",
                    )
                replay_evidence = route_replay.get("evidence", [])
                if not isinstance(replay_evidence, list) or len(replay_evidence) < 5:
                    result.error(
                        "SOLO_ROUTE_EVIDENCE",
                        "单人分流至少需要五个分层回放证据。",
                    )

    source_text = (case_dir / SOURCE).read_text(encoding="utf-8-sig")
    draft_text = (case_dir / DRAFT).read_text(encoding="utf-8-sig")
    full_text = (case_dir / FULL_DRAFT).read_text(encoding="utf-8-sig")
    issue_text = (case_dir / ISSUES).read_text(encoding="utf-8-sig")
    report_text = (case_dir / REPORT).read_text(encoding="utf-8-sig")
    coverage_text = (case_dir / COVERAGE).read_text(encoding="utf-8-sig")
    blocks = source_blocks(source_text)

    if markdown_field(report_text, "内容类型") not in {"单人分享", "单人开营分享"}:
        result.error("SOLO_TYPE_REQUIRED", "质检报告必须明确“内容类型：单人分享”（兼容旧单人开营分享）。")
    if not blocks:
        result.error("NO_TIMECODE_BLOCKS", "原稿没有识别到带说话人标签的时间块。")
        return result

    speakers = {speaker for speaker, _, _ in blocks}
    times = [to_seconds(timecode) for _, timecode, _ in blocks]
    result.metrics["source_blocks"] = len(blocks)
    route_confirms_one = verified_solo_route(case_dir, route)
    result.metrics["source_label_count"] = len(speakers)
    result.metrics["source_speakers"] = 1 if route_confirms_one else len(speakers)
    if len(speakers) != 1 and not route_confirms_one:
        result.error("NOT_EXACTLY_ONE_SPEAKER", f"原稿说话人={sorted(speakers)}")
    elif len(speakers) != 1:
        result.warning("SOURCE_LABELS_RECONCILED", "原稿含多个平台标签，本场回放已确认只有一个内容说话人；原标签继续保留追溯。")
    if any(current <= previous for previous, current in zip(times, times[1:])):
        result.error("TIMECODES_NOT_INCREASING", "原稿时间码必须严格递增。")

    named_speakers = set(re.findall(r"\*\*([^*\n：]{1,30})：\*\*", draft_text))
    if routed_solo:
        if named_speakers:
            result.error(
                "SOLO_SPEAKER_PREFIX_FORBIDDEN",
                f"统一入口生成的单人学习稿不使用逐段人名前缀：{sorted(named_speakers)}",
            )
        forbidden_intro = [
            phrase
            for phrase in ("整理状态：", "本文件名仅为", "以下为讲者")
            if phrase in draft_text
        ]
        if forbidden_intro:
            result.error(
                "SOLO_INTERNAL_INTRO_FORBIDDEN",
                f"学习稿开头保留了内部说明：{forbidden_intro}",
            )
    elif len(named_speakers) != 1:
        result.error("DRAFT_NOT_ONE_SPEAKER", f"旧版兼容稿署名={sorted(named_speakers)}")
    if re.search(r"发言人\s*2", draft_text):
        result.error("SECOND_SPEAKER_MARKER", "成稿出现“发言人2”。")

    if routed_solo:
        audio_path = case_dir / FULL_AUDIO
        audio_manifest_path = case_dir / AUDIO_MANIFEST
        if not audio_path.is_file():
            result.error("SOLO_FULL_AUDIO_REQUIRED", FULL_AUDIO)
        if not audio_manifest_path.is_file():
            result.error("SOLO_AUDIO_MANIFEST_REQUIRED", AUDIO_MANIFEST)
        if audio_path.is_file() and audio_manifest_path.is_file():
            try:
                audio_manifest = json.loads(
                    audio_manifest_path.read_text(encoding="utf-8-sig")
                )
                source_meta = audio_manifest.get("source", {})
                output_meta = audio_manifest.get("output", {})
                replay_path = case_dir / str(source_meta.get("path", ""))
                manifest_audio_path = case_dir / str(output_meta.get("path", ""))
                if not replay_path.is_file():
                    result.error(
                        "SOLO_AUDIO_SOURCE_MISSING",
                        f"整场音频manifest源文件不存在：{replay_path}",
                    )
                elif source_meta.get("sha256") != file_sha256(replay_path):
                    result.error(
                        "SOLO_AUDIO_SOURCE_HASH",
                        "原回放SHA-256与整场音频manifest不一致。",
                    )
                route_replay = route.get("replay", {})
                if (
                    isinstance(route_replay, dict)
                    and (
                        route_replay.get("path") != source_meta.get("path")
                        or route_replay.get("sha256") != source_meta.get("sha256")
                    )
                ):
                    result.error(
                        "SOLO_ROUTE_REPLAY_HASH",
                        "场次分流、原回放与整场音频manifest未绑定到同一份回放。",
                    )
                if manifest_audio_path.resolve() != audio_path.resolve():
                    result.error(
                        "SOLO_AUDIO_OUTPUT_PATH",
                        f"整场音频manifest输出路径应为 {FULL_AUDIO}。",
                    )
                elif output_meta.get("sha256") != file_sha256(audio_path):
                    result.error(
                        "SOLO_AUDIO_OUTPUT_HASH",
                        "整场音频SHA-256与manifest不一致。",
                    )
                source_duration = float(source_meta.get("duration_seconds", 0))
                output_duration = float(output_meta.get("duration_seconds", 0))
                tolerance = max(2.0, source_duration * 0.005)
                if (
                    source_duration <= 0
                    or output_duration <= 0
                    or abs(source_duration - output_duration) > tolerance
                ):
                    result.error(
                        "SOLO_AUDIO_DURATION",
                        f"整场音频时长核验失败：源={source_duration} 输出={output_duration}",
                    )
            except (AttributeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                result.error("INVALID_AUDIO_MANIFEST", str(exc))

    scope = read_content_scope(case_dir, source_text, full_text, handoff=handoff)
    for error in scope.errors:
        result.error("CONTENT_SCOPE_INVALID", error)
    for warning in scope.warnings:
        result.warning("CONTENT_SCOPE_PENDING", warning)
    source_spoken = "".join(body for _, _, body in source_blocks(scope.source))
    result.metrics['source_all_spoken_chars'] = len(compact_chars(''.join(body for _,_,body in blocks)))
    result.metrics['content_exclusions'] = len(scope.exclusions)
    draft_body = body_between_markers(draft_text)
    full_body = body_between_markers(scope.full_draft)
    if draft_body is None:
        result.error("DRAFT_BODY_MARKERS", f"成稿缺少 {BODY_START}/{BODY_END}。")
    if full_body is None:
        result.error("FULL_DRAFT_BODY_MARKERS", f"完整底稿缺少 {BODY_START}/{BODY_END}。")
    if draft_body is not None and full_body is not None:
        source_chars = len(compact_chars(source_spoken))
        draft_chars = len(compact_chars(draft_body))
        full_chars = len(compact_chars(full_body))
        source_ratio = draft_chars / source_chars if source_chars else 0.0
        full_ratio = draft_chars / full_chars if full_chars else 0.0
        result.metrics.update(
            {
                "source_spoken_chars": source_chars,
                "full_draft_chars": full_chars,
                "draft_body_chars": draft_chars,
                "draft_source_retention": source_ratio,
                "draft_full_retention": full_ratio,
            }
        )
        if source_ratio < 0.70:
            result.error(
                "SOURCE_RETENTION_LOW",
                f"正文/原口述={source_ratio:.2%}，要求至少70%。",
            )
        if full_ratio < 0.90:
            result.error(
                "FULL_DRAFT_RETENTION_LOW",
                f"正文/完整底稿={full_ratio:.2%}，要求至少90%。",
            )

    coverage_ranges = [
        (to_seconds(start), to_seconds(end))
        for start, end in re.findall(
            r"^\|\s*((?:\d{1,2}:)?\d{2}:\d{2})\s*[–—-]\s*((?:\d{1,2}:)?\d{2}:\d{2})\s*\|",
            coverage_text,
            re.MULTILINE,
        )
    ]
    missing_windows: list[str] = []
    for window_start in range(0, times[-1] + 1, 600):
        window_end = min(times[-1], window_start + 599)
        if not any(start <= window_end and end >= window_start for start, end in coverage_ranges):
            missing_windows.append(f"{window_start // 60:02d}:00–{window_end // 60:02d}:{window_end % 60:02d}")
    result.metrics["coverage_ranges"] = len(coverage_ranges)
    if missing_windows:
        result.error("TEN_MINUTE_COVERAGE_GAP", "缺少：" + "、".join(missing_windows))

    try:
        evidence = json.loads((case_dir / EVIDENCE).read_text(encoding="utf-8-sig"))
        sections = evidence.get("sections", [])
        if not isinstance(sections, list):
            raise ValueError("sections不是数组")
    except (json.JSONDecodeError, ValueError) as exc:
        result.error("INVALID_EVIDENCE_JSON", str(exc))
        sections = []
    result.metrics["evidence_modules"] = len(sections)
    if len(sections) < 6:
        result.error("EVIDENCE_MODULES_LOW", f"证据模块={len(sections)}，要求至少6。")
    previous_start = -1
    for section in sections:
        if not isinstance(section, dict):
            result.error("INVALID_EVIDENCE_MODULE", "证据模块必须为对象。")
            continue
        anchor = str(section.get("draft_anchor", "")).strip()
        if not anchor or anchor not in draft_text:
            result.error("EVIDENCE_ANCHOR_MISSING", f"{section.get('module_id', '?')} 定位句不在成稿。")
        ranges = section.get("source_ranges", [])
        if not isinstance(ranges, list) or not ranges:
            result.error("EVIDENCE_RANGE_MISSING", f"{section.get('module_id', '?')} 缺少来源时间段。")
            continue
        try:
            start = to_seconds(str(ranges[0]["start"]))
            end = to_seconds(str(ranges[-1]["end"]))
        except (KeyError, TypeError, ValueError) as exc:
            result.error("INVALID_EVIDENCE_RANGE", f"{section.get('module_id', '?')} {exc}")
            continue
        if start > end or start < previous_start:
            result.error("EVIDENCE_ORDER", f"{section.get('module_id', '?')} 来源时间段倒序。")
        previous_start = start

    for relative in (EVIDENCE, DECISIONS):
        try:
            json.loads((case_dir / relative).read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            result.error("INVALID_JSON", f"{relative}: {exc}")

    rows = parse_issue_rows(issue_text)
    invalid_statuses = sorted({row["status"] for row in rows} - ALLOWED_STATUSES)
    if invalid_statuses:
        result.error("INVALID_ISSUE_STATUS", f"非法状态={invalid_statuses}")
    open_p0 = [row for row in rows if row["priority"] == "P0" and row["status"] == "待确认"]
    open_p1 = [row for row in rows if row["priority"] == "P1" and row["status"] == "待确认"]
    result.metrics["open_p0"] = len(open_p0)
    result.metrics["open_p1"] = len(open_p1)
    if open_p0:
        result.error("OPEN_P0", f"未关闭P0={len(open_p0)}")

    clip_manifest = case_dir / "_work/review_clips/manifest.json"
    has_clips = clip_manifest.is_file()
    for row in open_p1:
        conservative = "保守表达" in row["action"] or "删除" in row["action"]
        if not conservative and not has_clips:
            result.error(
                "P1_WITHOUT_EVIDENCE_OR_CONSERVATIVE_TEXT",
                f"{row['timecode']} 未说明保守表达且没有回听音频。",
            )

    title = next((line[2:].strip() for line in draft_text.splitlines() if line.startswith("# ")), "")
    if TITLE_NUMBER_RE.search(title):
        title_issue = any(
            row["priority"] in {"P0", "P1"} and "标题数字" in row["type"] for row in rows
        )
        if not title_issue:
            result.error("TITLE_NUMBER_UNVERIFIED", f"标题含数字但没有P0/P1标题数字项：{title}")

    if handoff:
        if open_p1:
            result.error("SHARE_OPEN_P1", f"学员发送模式仍有P1={len(open_p1)}")
        if markdown_field(report_text, "评分性质") not in {"人工终审", "独立双轨评审"}:
            result.error("SHARE_HUMAN_REVIEW_REQUIRED", "学员发送模式评分性质必须为人工终审或独立双轨评审。")
        reviewer = markdown_field(report_text, "终审人")
        if not reviewer or reviewer in {"待填", "待填写", "无"}:
            result.error("SHARE_REVIEWER_REQUIRED", "学员发送模式必须填写真实终审人。")
        review_date = markdown_field(report_text, "终审日期") or ""
        if not valid_review_date(review_date):
            result.error("SHARE_DATE_REQUIRED", "学员发送模式必须填写YYYY-MM-DD终审日期。")
        readthrough = markdown_field(report_text, "学习稿独立通读")
        if readthrough is None:
            readthrough = markdown_field(report_text, "发布稿独立通读")
        if readthrough != "真人已完成":
            result.error("SHARE_READTHROUGH_REQUIRED", "学员发送模式要求真人独立通读。")
        if not (case_dir / "_work/终审基线/manifest.json").is_file():
            result.error("SHARE_BASELINE_REQUIRED", "学员发送模式缺少AI终审基线。")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="审计单人直播分享原声整理包。")
    parser.add_argument("case_dir", type=Path)
    gate = parser.add_mutually_exclusive_group()
    gate.add_argument("--share", action="store_true", help="启用真人终审后的学员发送门禁")
    gate.add_argument("--release", action="store_true", help="兼容旧命令，等同于 --share")
    args = parser.parse_args()
    result = audit_case(args.case_dir, share=args.share, release=args.release)
    for key, value in result.metrics.items():
        if isinstance(value, float):
            print(f"METRIC {key}={value:.4f}")
        else:
            print(f"METRIC {key}={value}")
    for warning in result.warnings:
        print(f"WARNING {warning}")
    for error in result.errors:
        print(f"ERROR {error}")
    print(f"AUDIT={'FAIL' if result.errors else 'PASS'}")
    print(f"errors={len(result.errors)} warnings={len(result.warnings)}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
