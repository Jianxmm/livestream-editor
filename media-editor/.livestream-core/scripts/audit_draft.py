#!/usr/bin/env python3
"""Audit a two-person livestream interview package before publication."""

from __future__ import annotations

import argparse
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from route_livestream import find_replay, valid_default_map
from speaker_analysis import SpeakerAnalysis
from review_validation import valid_review_date
from content_scope import read_content_scope
from speaker_analysis import build_speaker_analysis
from typing import Iterable


SOURCE_NAME = "02_带时间码原稿.md"
DRAFT_NAME = "01_公众号可发布稿.md"
ISSUES_NAME = "02_待核问题清单.md"
REPORT_NAME = "03_编辑质检报告.md"
EVIDENCE_NAME = "证据映射.json"
COVERAGE_NAME = "内容覆盖表.md"
FULL_DRAFT_NAME = "完整编辑底稿.md"
EDIT_LEDGER_NAME = "编辑决策台账.json"
REVIEW_SHEET_NAME = "回听终审表.md"
REVIEW_INDEX_NAME = "回听辅助索引.md"
SPEAKER_CALIBRATION_NAME = "说话人校准.json"
SPEAKER_TIMELINE_NAME = "说话人时间轴.json"
SPEAKER_CONFLICTS_NAME = "说话人冲突清单.md"

TIMESTAMP_RE = re.compile(r"(?<!\d)(?:\d{1,2}:)?\d{2}:\d{2}(?!\d)")
SEMANTIC_END_RE = re.compile(
    r"发布稿语义结束点\s*[：:]\s*((?:\d{1,2}:)?\d{2}:\d{2})"
)
SOURCE_SPEAKER_RE = re.compile(
    r"^发言人\s*([^\s]+)\s+((?:\d{1,2}:)?\d{2}:\d{2})\s*$", re.MULTILINE
)
NAMED_SPEAKER_RE = re.compile(
    r"^\*\*([^*\n：:]{1,20})[：:]\*\*", re.MULTILINE
)
DRAFT_TURN_RE = re.compile(
    r"^\*\*([^*\n：:]{1,20})[：:]\*\*\s*(.*?)"
    r"(?=^\*\*[^*\n：:]{1,20}[：:]\*\*|^##\s|^---$|\Z)",
    re.MULTILINE | re.DOTALL,
)
NON_SPEAKER_LABELS = {"以下文字版", "以下为访谈正文", "作者", "编辑", "来源"}
UNFINISHED_RE = re.compile(
    r"\b(?:TODO|TBD|XXX)\b|待填写|待补充|运营素材待补|"
    r"【待(?:补|填|确认)[^\u3011]*】|\?{3,}",
    re.IGNORECASE,
)
SHUTDOWN_TAIL_PATTERNS = {
    "截图或合影": re.compile(r"截个?图|截图|合影"),
    "镜头摆位": re.compile(r"看这里|看那里|看镜头|小宝贝"),
    "外貌或美颜": re.compile(r"我现在好白|你这么白|涂了防晒|美颜"),
    "关播确认": re.compile(r"应该是结束了|关播|下播|结束了吗"),
    "现场道具": re.compile(r"小黄鸭|咑们干杯"),
}
ENDING_RUNWAY_PATTERNS = {
    "时间或不舍转场": re.compile(r"怎么时间过得这么快|时间过得真快|我也舍不得|聊着聊着快结束"),
    "咖啡或睡眠闲聊": re.compile(r"咖啡|双倍浓缩|喝了就睡不着"),
    "现场征友玩笑": re.compile(r"单身女性|看上.{0,8}帅小伙|私下勾搭|奔着这个目的|先摆明.{0,12}单身"),
}
INTRO_INTERNAL_JARGON_RE = re.compile(
    r"根据(?:本场)?直播原稿|轻度校订|机器转写|说话人校准|发言块|保留率|AI(?:整理|生成)"
)
ARABIC_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?(?![A-Za-z0-9.])")
CHINESE_NUMBER_RE = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+")
RISK_PATTERNS = {
    "医疗或功效承诺": re.compile(r"治愈|疗效|包治|药到病除|所有人都应该"),
    "收益或结果承诺": re.compile(
        r"保证.{0,8}(?:赚|收入|结果)|(?<!不是)百分之百.{0,4}(?:能|会|成功|有效|赚钱)|一定能赚|永远能赚"
    ),
    "排名或资质声称": re.compile(r"世界前\d+|全国第一|行业第一|唯一一家"),
}
MIN_IMMERSIVE_RETENTION_RATIO = 0.50
TARGET_IMMERSIVE_RETENTION_RATIO = 0.60
MIN_TURN_RETENTION_RATIO = 0.30
TARGET_TURN_RETENTION_RATIO = 0.35
MIN_LONG_INTERVIEW_CHARS = 10_000
MIN_FULL_DRAFT_CONTENT_RETENTION = 0.90
MIN_DUPLICATE_PARAGRAPH_CHARS = 80
MIN_GROUNDING_PARAGRAPHS = 10
MIN_GROUNDING_MEDIAN = 0.45
TARGET_GROUNDING_MEDIAN = 0.65
TARGET_GROUNDING_P10 = 0.50
MAX_LOW_GROUNDING_SHARE = 0.08
LOW_GROUNDING_THRESHOLD = 0.25
TIMELINE_BUCKET_SECONDS = 15 * 60
MIN_LOCAL_RETENTION_RATIO = 0.30
TARGET_LOCAL_RETENTION_RATIO = 0.40
MAX_LOCAL_RETENTION_RATIO = 0.90
MIN_LOCAL_SOURCE_CHARS = 500
MAX_SPEAKER_RETENTION_GAP = 0.20
TARGET_SPEAKER_RETENTION_GAP = 0.10
MIN_SINGLE_SPEAKER_RETENTION = 0.40
TARGET_SINGLE_SPEAKER_RETENTION = 0.50
LONG_DIALOGUE_PARAGRAPH_CHARS = 300
MAX_DIALOGUE_PARAGRAPH_CHARS = 500
LOW_CONFIDENCE_SPEAKER_SCORE = 0.18
LOW_CONFIDENCE_SPEAKER_GAP = 0.15
SPEAKER_ALIGNMENT_REVIEW_DRIFT_SECONDS = 3
SPEAKER_ALIGNMENT_MAX_DRIFT_SECONDS = 15
SOURCE_ORDER_MATCH_SCORE = 0.30
SOURCE_ORDER_MATCH_MARGIN = 0.10
SOURCE_ORDER_BACKTRACK_SECONDS = 90
SOURCE_ORDER_MAX_BACKTRACK_SECONDS = 4 * 60
ALLOWED_ISSUE_STATUSES = {"待确认", "已回听", "已当事人确认", "已删除", "已解决"}
ALLOWED_SPEAKER_BLOCK_STATUSES = {
    "一致",
    "声学归属",
    "真人已确认",
    "冲突待核",
    "混合说话人",
    "无法映射",
}
CONFIRMED_SPEAKER_REVIEW_STATUSES = {"已确认", "已回听", "已当事人确认"}
ALLOWED_EDIT_TYPES = {
    "说话人纠正",
    "说话人边界还原",
    "ASR语义修复",
    "专名纠正",
    "事实规范化",
    "隐私处理",
    "内容移动",
}
ALLOWED_EDIT_BASES = {
    "回放音频",
    "回放画面",
    "带时间码原稿",
    "原稿上下文",
    "机器辅助转写",
    "专名表",
    "背景资料",
    "隐私规则",
}
ALLOWED_EVIDENCE_STATUSES = {"已核对原稿", "部分待回听", "待回听", "已回听"}


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str
    file: str = ""


def replay_relative(case_dir: Path) -> str:
    try:
        return find_replay(case_dir).relative_to(case_dir).as_posix()
    except ValueError:
        return "inputs/01_直播回放.mp4"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def time_to_seconds(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def format_timecode(value: int) -> str:
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def chinese_number_to_int(value: str) -> int | None:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    small_units = {"十": 10, "百": 100, "千": 1000}
    large_units = {"万": 10_000, "亿": 100_000_000}
    if not value or any(char not in digits | small_units | large_units for char in value):
        return None
    if all(char in digits for char in value):
        try:
            return int("".join(str(digits[char]) for char in value))
        except ValueError:
            return None

    total = 0
    section = 0
    number = 0
    for char in value:
        if char in digits:
            number = digits[char]
        elif char in small_units:
            unit = small_units[char]
            section += (number or 1) * unit
            number = 0
        else:
            section += number
            total += (section or 1) * large_units[char]
            section = 0
            number = 0
    return total + section + number


def supported_number_values(text: str) -> set[float]:
    values: set[float] = set()
    for match in ARABIC_NUMBER_RE.finditer(text):
        values.add(float(match.group()))
    # Chinese livestream transcripts often shorten recent years to ``13年`` or
    # ``24年`` while the edited article expands them to ``2013年`` and
    # ``2024年``. Only add that expansion when the same source already contains
    # an explicit 20xx year, so ordinary durations such as ``14年`` are not
    # globally treated as calendar years in unrelated material.
    if re.search(r"(?<!\d)20\d{2}年", text):
        for match in re.finditer(r"(?<!\d)(\d{2})年", text):
            values.add(float(2000 + int(match.group(1))))
    for match in CHINESE_NUMBER_RE.finditer(text):
        converted = chinese_number_to_int(match.group())
        if converted is not None:
            values.add(float(converted))
    return values


def remove_section_ordinals(text: str) -> str:
    return re.sub(r"^(##\s+)\d{1,2}(?=<br>|\s|$)", r"\1", text, flags=re.MULTILINE)


def normalize_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def non_whitespace_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def declared_semantic_end_seconds(coverage: str) -> int | None:
    """Return the declared publication cutoff, when the coverage map has one."""
    match = SEMANTIC_END_RE.search(coverage)
    return time_to_seconds(match.group(1)) if match else None


def source_interview_content(source: str, end_seconds: int | None = None) -> str:
    matches = list(SOURCE_SPEAKER_RE.finditer(source))
    if not matches:
        return non_whitespace_text(source)
    chunks = [
        source[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(source)]
        for index, match in enumerate(matches)
        if end_seconds is None or time_to_seconds(match.group(2)) < end_seconds
    ]
    return non_whitespace_text("".join(chunks))


def draft_interview_content(draft: str) -> str:
    chunks: list[str] = []
    for match in DRAFT_TURN_RE.finditer(draft):
        if match.group(1) in NON_SPEAKER_LABELS:
            continue
        lines = [
            line
            for line in match.group(2).splitlines()
            if not line.lstrip().startswith(("#", "> [", "[", "---"))
        ]
        chunks.append("\n".join(lines))
    return non_whitespace_text("".join(chunks))


def source_turn_records(source: str) -> list[tuple[str, str, int]]:
    matches = list(SOURCE_SPEAKER_RE.finditer(source))
    records: list[tuple[str, str, int]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        records.append(
            (
                f"发言人{match.group(1)}",
                normalize_text(source[match.end() : end]),
                time_to_seconds(match.group(2)),
            )
        )
    return records


def draft_speaker_paragraphs(draft: str) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    for match in DRAFT_TURN_RE.finditer(draft):
        speaker = match.group(1)
        if speaker in NON_SPEAKER_LABELS:
            continue
        for paragraph in re.split(r"\n\s*\n", match.group(2)):
            clean = normalize_text(paragraph)
            if len(clean) >= 12:
                records.append((speaker, clean))
    return records


def timeline_retention_profile(
    source: str,
    draft: str,
    speaker_map: dict[str, str],
    source_end_seconds: int | None = None,
) -> list[tuple[int, int, float, int, int]]:
    """Estimate local retention by monotonically aligning edited paragraphs to source turns."""
    source_records = [
        (str(speaker_map.get(speaker, speaker)), text, second, character_ngrams(text))
        for speaker, text, second in source_turn_records(source)
        if source_end_seconds is None or second < source_end_seconds
    ]
    draft_records = [
        (speaker, text, character_ngrams(text))
        for speaker, text in draft_speaker_paragraphs(draft)
    ]
    if not source_records or not draft_records:
        return []

    negative = -1e12
    previous = [0.0] * len(source_records)
    backtrack: list[list[int]] = []
    for draft_speaker, _, draft_grams in draft_records:
        prefix: list[tuple[float, int]] = []
        best_value = negative
        best_index = 0
        for index, value in enumerate(previous):
            if value > best_value:
                best_value = value
                best_index = index
            prefix.append((best_value, best_index))
        current = [negative] * len(source_records)
        row = [0] * len(source_records)
        for index, (source_speaker, _, _, source_grams) in enumerate(source_records):
            if source_speaker != draft_speaker or not draft_grams or not source_grams:
                continue
            score = len(draft_grams & source_grams) / min(
                len(draft_grams), len(source_grams)
            )
            current[index] = prefix[index][0] + score
            row[index] = prefix[index][1]
        if max(current) <= negative / 2:
            return []
        previous = current
        backtrack.append(row)

    source_index = max(range(len(previous)), key=previous.__getitem__)
    alignment = [0] * len(draft_records)
    for draft_index in range(len(draft_records) - 1, -1, -1):
        alignment[draft_index] = source_index
        source_index = backtrack[draft_index][source_index]

    interview_end = max(record[2] for record in source_records)
    bucket_count = math.ceil((interview_end + 1) / TIMELINE_BUCKET_SECONDS)
    source_chars = [0] * bucket_count
    draft_chars = [0] * bucket_count
    for _, text, second, _ in source_records:
        source_chars[min(second // TIMELINE_BUCKET_SECONDS, bucket_count - 1)] += len(text)
    for (_, text, _), aligned_index in zip(draft_records, alignment):
        second = source_records[aligned_index][2]
        draft_chars[min(second // TIMELINE_BUCKET_SECONDS, bucket_count - 1)] += len(text)

    profile: list[tuple[int, int, float, int, int]] = []
    for bucket, (source_count, draft_count) in enumerate(zip(source_chars, draft_chars)):
        if source_count < MIN_LOCAL_SOURCE_CHARS:
            continue
        start = bucket * TIMELINE_BUCKET_SECONDS
        end = start + TIMELINE_BUCKET_SECONDS
        profile.append((start, end, draft_count / source_count, source_count, draft_count))
    return profile


def audit_timeline_retention(
    source: str,
    draft: str,
    edit_ledger: str,
    path: Path,
    coverage: str = "",
) -> list[Finding]:
    if len(source_interview_content(source)) < MIN_LONG_INTERVIEW_CHARS:
        return []
    try:
        speaker_map = json.loads(edit_ledger).get("speaker_map", {})
    except json.JSONDecodeError:
        return []
    if not isinstance(speaker_map, dict):
        return []
    profile = timeline_retention_profile(
        source,
        draft,
        speaker_map,
        source_end_seconds=declared_semantic_end_seconds(coverage),
    )
    under = [item for item in profile if item[2] < MIN_LOCAL_RETENTION_RATIO]
    thin = [
        item
        for item in profile
        if MIN_LOCAL_RETENTION_RATIO <= item[2] < TARGET_LOCAL_RETENTION_RATIO
    ]
    over = [item for item in profile if item[2] > MAX_LOCAL_RETENTION_RATIO]

    def describe(items: list[tuple[int, int, float, int, int]]) -> str:
        return "、".join(
            f"{format_timecode(start)}–{format_timecode(end)}={ratio:.1%}"
            for start, end, ratio, _, _ in items[:8]
        )

    findings: list[Finding] = []
    if under:
        findings.append(
            Finding(
                "ERROR",
                "LOCAL_TIMELINE_OVERCOMPRESSED",
                "全稿总保留率无法掩盖局部时段被压成摘要；以下15分钟窗口低于30%："
                + describe(under),
                str(path),
            )
        )
    if thin:
        findings.append(
            Finding(
                "WARNING",
                "LOCAL_TIMELINE_THIN",
                "以下15分钟窗口保留率低于40%，需复查追问链、场景和限定条件："
                + describe(thin),
                str(path),
            )
        )
    if over:
        findings.append(
            Finding(
                "WARNING",
                "LOCAL_TIMELINE_OVEREXPANDED",
                "以下15分钟窗口成稿量超过原稿90%，需检查重复、错配或编辑扩写："
                + describe(over),
                str(path),
            )
        )
    return findings


def audit_speaker_retention_balance(
    source: str,
    draft: str,
    edit_ledger: str,
    path: Path,
) -> list[Finding]:
    if len(source_interview_content(source)) < MIN_LONG_INTERVIEW_CHARS:
        return []
    try:
        speaker_map = json.loads(edit_ledger).get("speaker_map", {})
    except json.JSONDecodeError:
        return []
    if not isinstance(speaker_map, dict) or len(speaker_map) != 2:
        return []
    source_chars = {str(name): 0 for name in speaker_map.values()}
    for raw_speaker, text, _ in source_turn_records(source):
        name = speaker_map.get(raw_speaker)
        if name in source_chars:
            source_chars[str(name)] += len(text)
    draft_chars = {name: 0 for name in source_chars}
    for speaker, text in draft_speaker_paragraphs(draft):
        if speaker in draft_chars:
            draft_chars[speaker] += len(text)
    rates = {
        speaker: draft_chars[speaker] / source_count
        for speaker, source_count in source_chars.items()
        if source_count >= 1000
    }
    if len(rates) != 2:
        return []
    gap = max(rates.values()) - min(rates.values())
    lowest = min(rates.values())
    detail = "、".join(f"{speaker}{rate:.1%}" for speaker, rate in rates.items())
    findings: list[Finding] = []
    if lowest < MIN_SINGLE_SPEAKER_RETENTION:
        findings.append(
            Finding(
                "ERROR",
                "SPEAKER_RETENTION_TOO_LOW",
                f"至少一位说话人的字符保留率低于{MIN_SINGLE_SPEAKER_RETENTION:.0%}，即使两人比例接近也可能把对谈压薄：{detail}",
                str(path),
            )
        )
    elif lowest < TARGET_SINGLE_SPEAKER_RETENTION:
        findings.append(
            Finding(
                "WARNING",
                "SPEAKER_RETENTION_LOW",
                f"至少一位说话人的字符保留率低于{TARGET_SINGLE_SPEAKER_RETENTION:.0%}，请复查其判断、回应、限定和人物细节：{detail}",
                str(path),
            )
        )
    if gap > MAX_SPEAKER_RETENTION_GAP:
        findings.append(
            Finding(
                "ERROR",
                "SPEAKER_RETENTION_IMBALANCE",
                f"两位说话人的字符保留率相差{gap:.1%}，可能把一方压成工具人：{detail}",
                str(path),
            )
        )
    elif gap > TARGET_SPEAKER_RETENTION_GAP:
        findings.append(
            Finding(
                "WARNING",
                "SPEAKER_RETENTION_REVIEW",
                f"两位说话人的字符保留率相差{gap:.1%}，请确认差异来自有效去重：{detail}",
                str(path),
            )
        )
    return findings


def character_ngrams(text: str, size: int = 3) -> set[str]:
    if len(text) < size:
        return set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def audit_speaker_attribution(
    source: str,
    draft: str,
    edit_ledger: str,
    path: Path,
) -> list[Finding]:
    try:
        ledger = json.loads(edit_ledger)
    except json.JSONDecodeError:
        return []
    speaker_map = ledger.get("speaker_map")
    if not isinstance(speaker_map, dict):
        return []
    source_records = source_turn_records(source)
    if not source_records:
        return []
    tracked_speaker_ranges: list[tuple[int, int]] = []
    for item in ledger.get("nontrivial_edits", []):
        if not isinstance(item, dict) or item.get("type") != "说话人纠正":
            continue
        start = item.get("start")
        end = item.get("end")
        if isinstance(start, str) and isinstance(end, str) and TIMESTAMP_RE.fullmatch(start) and TIMESTAMP_RE.fullmatch(end):
            tracked_speaker_ranges.append((time_to_seconds(start), time_to_seconds(end)))

    mismatches: list[str] = []
    low_confidence_mismatches: list[str] = []
    for match in DRAFT_TURN_RE.finditer(draft):
        draft_speaker = match.group(1)
        if draft_speaker in NON_SPEAKER_LABELS:
            continue
        clean = normalize_text(match.group(2))
        draft_grams = character_ngrams(clean)
        if len(draft_grams) < 12:
            continue
        best: tuple[float, str, int] | None = None
        best_same_score = 0.0
        for source_speaker, source_text, source_second in source_records:
            source_grams = character_ngrams(source_text)
            if len(source_grams) < 6:
                continue
            shared = len(draft_grams & source_grams)
            score = shared / min(len(draft_grams), len(source_grams))
            mapped_speaker = speaker_map.get(source_speaker)
            if mapped_speaker == draft_speaker:
                best_same_score = max(best_same_score, score)
            if best is None or score > best[0]:
                best = (score, str(mapped_speaker or source_speaker), source_second)
        if best is None:
            continue
        best_score, best_speaker, source_second = best
        if best_speaker == draft_speaker:
            continue
        if any(start <= source_second <= end for start, end in tracked_speaker_ranges):
            continue
        detail = (
            f"{draft_speaker}段更像{best_speaker}在{format_timecode(source_second)}的原话"
            f"（匹配{best_score:.0%}，本说话人最佳{best_same_score:.0%}）：{clean[:60]}"
        )
        if best_score >= 0.70 and best_score - best_same_score >= 0.15:
            mismatches.append(detail)
        elif (
            best_score >= LOW_CONFIDENCE_SPEAKER_SCORE
            and best_score - best_same_score >= LOW_CONFIDENCE_SPEAKER_GAP
            and best_same_score <= 0.10
        ):
            low_confidence_mismatches.append(detail)
    findings: list[Finding] = []
    if mismatches:
        findings.append(
            Finding(
                "ERROR",
                "SPEAKER_ATTRIBUTION_MISMATCH",
                f"发现 {len(mismatches)} 个高置信度说话人归属冲突：" + "；".join(mismatches[:8]),
                str(path),
            )
        )
    if low_confidence_mismatches:
        findings.append(
            Finding(
                "WARNING",
                "LOW_CONFIDENCE_SPEAKER_ATTRIBUTION",
                "发现改写后更像另一位说话人的低置信度段落；可能是正常复述，也可能是编辑替人总结，须逐段对照："
                + "；".join(low_confidence_mismatches[:8]),
                str(path),
            )
        )
    return findings


def audit_suspicious_speaker_sandwiches(
    source: str,
    edit_ledger: str,
    path: Path,
) -> list[Finding]:
    """Flag short A-B-A source turns that may be one speaker split by ASR."""
    try:
        ledger = json.loads(edit_ledger)
    except json.JSONDecodeError:
        return []
    confirmed_ranges: list[tuple[int, int]] = []
    for item in ledger.get("nontrivial_edits", []):
        if not isinstance(item, dict) or item.get("type") != "说话人纠正":
            continue
        if item.get("status") != "已解决":
            continue
        start = item.get("start")
        end = item.get("end", start)
        if isinstance(start, str) and isinstance(end, str):
            confirmed_ranges.append((time_to_seconds(start), time_to_seconds(end)))

    records = source_turn_records(source)
    candidates: list[str] = []
    continuation_re = re.compile(r"^(?:然后|又|也|而且|所以|因为|我|对吧|对我|在尝试)")
    first_person_re = re.compile(r"^(?:对)?(?:然后|后来|所以|而且|其实|那)?我")
    for previous, middle, following in zip(records, records[1:], records[2:]):
        if previous[0] != following[0] or previous[0] == middle[0]:
            continue
        if middle[2] - previous[2] > 20 or following[2] - middle[2] > 20:
            continue
        if not 4 <= len(middle[1]) <= 45 or not first_person_re.search(middle[1]):
            continue
        middle_grams = character_ngrams(middle[1])
        following_grams = character_ngrams(following[1])
        if not middle_grams or not following_grams:
            continue
        continuity = len(middle_grams & following_grams) / min(
            len(middle_grams), len(following_grams)
        )
        if continuity < 0.10 or not continuation_re.search(following[1]):
            continue
        if any(start <= middle[2] <= end for start, end in confirmed_ranges):
            continue
        candidates.append(
            f"{format_timecode(middle[2])} {middle[0]}“{middle[1][:30]}”夹在两段{previous[0]}发言之间"
        )

    if not candidates:
        return []
    return [
        Finding(
            "WARNING",
            "SUSPICIOUS_SPEAKER_SANDWICH",
            "原转写存在疑似A-B-A夹心式串台；不得按标签直接定稿，须结合上下文并回听："
            + "；".join(candidates[:8]),
            str(path),
        )
    ]


def audit_source_order(
    source: str,
    draft: str,
    edit_ledger: str,
    path: Path,
) -> list[Finding]:
    """Detect high-enough source matches that move backwards in the published order."""
    try:
        ledger = json.loads(edit_ledger)
    except json.JSONDecodeError:
        return []
    speaker_map = ledger.get("speaker_map")
    if not isinstance(speaker_map, dict):
        return []
    source_records = [
        (str(speaker_map.get(speaker, speaker)), text, second, character_ngrams(text))
        for speaker, text, second in source_turn_records(source)
    ]
    movement_ranges: list[tuple[int, int]] = []
    for item in ledger.get("nontrivial_edits", []):
        if not isinstance(item, dict) or item.get("type") != "内容移动":
            continue
        start, end = item.get("start"), item.get("end")
        if isinstance(start, str) and isinstance(end, str) and TIMESTAMP_RE.fullmatch(start) and TIMESTAMP_RE.fullmatch(end):
            movement_ranges.append((time_to_seconds(start), time_to_seconds(end)))

    matches: list[tuple[int, float, str, str]] = []
    for draft_speaker, text in draft_speaker_paragraphs(draft):
        draft_grams = character_ngrams(text)
        if not draft_grams:
            continue
        candidates: list[tuple[float, int]] = []
        for source_speaker, _, second, source_grams in source_records:
            if source_speaker != draft_speaker or not source_grams:
                continue
            score = len(draft_grams & source_grams) / min(len(draft_grams), len(source_grams))
            candidates.append((score, second))
        if not candidates:
            continue
        candidates.sort(reverse=True)
        score, second = candidates[0]
        alternative_score = max(
            (
                candidate_score
                for candidate_score, candidate_second in candidates[1:]
                if abs(candidate_second - second) > SOURCE_ORDER_BACKTRACK_SECONDS
            ),
            default=0.0,
        )
        if (
            score >= SOURCE_ORDER_MATCH_SCORE
            and score - alternative_score >= SOURCE_ORDER_MATCH_MARGIN
        ):
            matches.append((second, score, draft_speaker, text[:60]))

    reversals: list[str] = []
    previous: tuple[int, float, str, str] | None = None
    for current in matches:
        backwards = previous[0] - current[0] if previous is not None else 0
        if (
            previous is not None
            and SOURCE_ORDER_BACKTRACK_SECONDS < backwards <= SOURCE_ORDER_MAX_BACKTRACK_SECONDS
        ):
            covered = any(
                start <= previous[0] <= end or start <= current[0] <= end
                for start, end in movement_ranges
            )
            if not covered:
                reversals.append(
                    f"{format_timecode(previous[0])}后倒回{format_timecode(current[0])}："
                    f"{current[2]}“{current[3]}”"
                )
        previous = current
    if not reversals:
        return []
    return [
        Finding(
            "ERROR",
            "DRAFT_SOURCE_ORDER_REVERSAL",
            "发现成稿问答链与原直播时间顺序倒置，且编辑台账没有内容移动记录："
            + "；".join(reversals[:8]),
            str(path),
        )
    ]


def retention_ratio(
    source: str,
    draft: str,
    source_end_seconds: int | None = None,
) -> float | None:
    source_content = source_interview_content(source, end_seconds=source_end_seconds)
    if not source_content:
        return None
    draft_content = draft_interview_content(draft)
    return len(draft_content) / len(source_content)


def audit_retention(source: str, draft: str, path: Path, coverage: str = "", *, source_turn_count: int | None = None) -> list[Finding]:
    """Block long-form drafts that no longer function as immersive transcripts."""
    semantic_end = declared_semantic_end_seconds(coverage)
    ratio = retention_ratio(source, draft, source_end_seconds=semantic_end)
    if ratio is None:
        return []
    source_chars = len(source_interview_content(source, end_seconds=semantic_end))
    source_turns = sum(
        1
        for match in SOURCE_SPEAKER_RE.finditer(source)
        if semantic_end is None or time_to_seconds(match.group(2)) < semantic_end
    )
    if source_turn_count is not None:
        source_turns = source_turn_count
    draft_turns = sum(
        1 for match in DRAFT_TURN_RE.finditer(draft) if match.group(1) not in NON_SPEAKER_LABELS
    )
    turn_ratio = draft_turns / source_turns if source_turns else None
    findings: list[Finding] = []
    if source_chars < MIN_LONG_INTERVIEW_CHARS:
        return findings
    if ratio < MIN_IMMERSIVE_RETENTION_RATIO:
        findings.append(
            Finding(
                "ERROR",
                "OVER_COMPRESSED_IMMERSIVE_DRAFT",
                f"原声沉浸稿仅保留原稿纯问答字符的 {ratio:.2%}，低于 "
                f"{MIN_IMMERSIVE_RETENTION_RATIO:.0%} 硬下限；不能以“核心主题都在”放行。",
                str(path),
            )
        )
    elif ratio < TARGET_IMMERSIVE_RETENTION_RATIO:
        findings.append(
            Finding(
                "WARNING",
                "LOW_IMMERSIVE_RETENTION",
                f"原声沉浸稿纯问答保留率为 {ratio:.2%}，低于 "
                f"{TARGET_IMMERSIVE_RETENTION_RATIO:.0%} 复查线；请逐条检查追问链、细节和人物原声。",
                str(path),
            )
        )
    if turn_ratio is not None and turn_ratio < MIN_TURN_RETENTION_RATIO:
        findings.append(
            Finding(
                "ERROR",
                "TOO_FEW_DIALOGUE_TURNS",
                f"成稿保留 {draft_turns}/{source_turns} 个发言块（{turn_ratio:.2%}），低于 "
                f"{MIN_TURN_RETENTION_RATIO:.0%} 硬下限；主持人推进和自然往返可能被过度合并。",
                str(path),
            )
        )
    elif turn_ratio is not None and turn_ratio < TARGET_TURN_RETENTION_RATIO:
        findings.append(
            Finding(
                "WARNING",
                "LOW_DIALOGUE_TURN_RETENTION",
                f"成稿发言块保留率为 {turn_ratio:.2%}，低于 {TARGET_TURN_RETENTION_RATIO:.0%} 复查线。",
                str(path),
            )
        )
    return findings


def audit_full_draft(full_draft: str, draft: str, path: Path) -> list[Finding]:
    """Ensure publication keeps nearly all meaningful dialogue from the full edit."""
    full_chars = len(draft_interview_content(full_draft))
    draft_chars = len(draft_interview_content(draft))
    if not full_chars:
        return [
            Finding(
                "ERROR",
                "FULL_DRAFT_NO_DIALOGUE",
                "完整编辑底稿未识别到 `**姓名：**` 格式的双人问答，无法比较发布稿保留率。",
                str(path),
            )
        ]
    if draft_chars / full_chars >= MIN_FULL_DRAFT_CONTENT_RETENTION:
        return []
    return [
        Finding(
            "ERROR",
            "PUBLISHED_DROPS_FULL_DRAFT",
            f"发布稿仅保留完整编辑底稿纯问答内容的 {draft_chars / full_chars:.2%}，"
            f"低于 {MIN_FULL_DRAFT_CONTENT_RETENTION:.0%} 下限；"
            "完整底稿不是供二次大删的素材池，请补回有效问答。",
            str(path),
        )
    ]


def duplicate_paragraphs(text: str) -> list[str]:
    """Return exact repeated long paragraphs after light Markdown normalization."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        stripped = paragraph.strip()
        if not stripped or stripped.startswith(("#", "> [", "[", "---")):
            continue
        stripped = NAMED_SPEAKER_RE.sub("", stripped).strip()
        normalized = normalize_text(stripped)
        if len(normalized) < MIN_DUPLICATE_PARAGRAPH_CHARS:
            continue
        if normalized in seen and stripped not in duplicates:
            duplicates.append(stripped)
        seen.add(normalized)
    return duplicates


def near_duplicate_paragraphs(text: str) -> list[str]:
    """Find close same-speaker paragraphs that may repeat one source passage."""
    records: list[tuple[int, str, str, str]] = []
    current_speaker = ""
    for paragraph_index, paragraph in enumerate(re.split(r"\n\s*\n", text)):
        stripped = paragraph.strip()
        if not stripped:
            continue
        if stripped.startswith(("#", "> [", "[", "---")):
            if stripped.startswith("#"):
                current_speaker = ""
            continue
        speaker = NAMED_SPEAKER_RE.match(stripped)
        if speaker:
            if speaker.group(1) in NON_SPEAKER_LABELS:
                current_speaker = ""
                continue
            current_speaker = speaker.group(1)
            stripped = NAMED_SPEAKER_RE.sub("", stripped).strip()
        if not current_speaker:
            continue
        normalized = normalize_text(stripped)
        if len(normalized) >= 55:
            records.append((paragraph_index, current_speaker, normalized, stripped))

    matches: list[str] = []
    for index, first in enumerate(records):
        for second in records[index + 1 : index + 41]:
            if first[1] != second[1] or first[2] == second[2]:
                continue
            length_ratio = min(len(first[2]), len(second[2])) / max(
                len(first[2]), len(second[2])
            )
            if length_ratio < 0.55:
                continue
            similarity = SequenceMatcher(
                None, first[2], second[2], autojunk=False
            ).ratio()
            if similarity >= 0.68:
                matches.append(
                    f"{first[1]}：{first[3][:42]} ↔ {second[3][:42]}（{similarity:.0%}）"
                )
    return matches


def paragraph_grounding_profile(draft: str, support: str) -> tuple[list[float], list[str]]:
    """Measure whether the body still resembles line-edited speech rather than a rewrite.

    This is deliberately calibrated as a corpus-level alarm. A single repaired ASR
    paragraph can legitimately differ from the transcript, but an immersive draft
    whose whole distribution moves far away from the source has crossed from light
    copy-editing into ghostwriting.
    """
    first_section = re.search(r"^##\s+", draft, re.MULTILINE)
    if first_section:
        draft = draft[first_section.start() :]
    support_clean = normalize_text(support)
    support_grams = {
        support_clean[index : index + 3]
        for index in range(max(0, len(support_clean) - 2))
    }
    ratios: list[float] = []
    warnings: list[str] = []
    for paragraph in re.split(r"\n\s*\n", draft):
        stripped = paragraph.strip()
        if not stripped or stripped.startswith(("#", "> [", "[", "---")):
            continue
        stripped = NAMED_SPEAKER_RE.sub("", stripped).strip()
        clean = normalize_text(stripped)
        if len(clean) < 40:
            continue
        grams = [clean[index : index + 3] for index in range(len(clean) - 2)]
        overlap = sum(gram in support_grams for gram in grams) / len(grams)
        ratios.append(overlap)
        if overlap < LOW_GROUNDING_THRESHOLD:
            warnings.append(f"来源重合度 {overlap:.0%}：{stripped[:80]}")
    return ratios, warnings


def candidate_terms(text: str) -> set[str]:
    flattened = {
        item
        for pair in re.findall(r"《([^》]{2,30})》|「([^」]{2,30})」", text)
        for item in pair
        if item
    }
    # Broad Chinese suffix matching produced fragments such as “那门公众号”,
    # “上老师” and “在老师”. Chinese people and organizations are
    # therefore enforced by the two-level glossary, while this heuristic is
    # limited to explicitly marked titles and distinctive Latin tokens.
    flattened.update(
        token
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9.-]{1,30}\b", text)
        if token.lower() not in {"br", "end", "http", "https", "markdown"}
    )
    return flattened


def split_term_variants(value: str) -> list[str]:
    if value.strip() in {"", "—", "-", "无", "暂无"}:
        return []
    return [item.strip() for item in re.split(r"[、,，/]+", value) if item.strip()]


def confirmed_term_rules(text: str) -> list[tuple[str, str]]:
    """Read confirmed ``wrong -> canonical`` rules from a Markdown table."""
    rules: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6 or cells[4] != "已确认":
            continue
        canonical = cells[0]
        for wrong in split_term_variants(cells[2]):
            if wrong != canonical:
                rules.append((wrong, canonical))
    return rules


def audit_proper_nouns(draft: str, term_sources: Iterable[tuple[Path, str]], path: Path) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for _, text in term_sources:
        for wrong, canonical in confirmed_term_rules(text):
            if (wrong, canonical) in seen or wrong not in draft:
                continue
            seen.add((wrong, canonical))
            findings.append(
                Finding(
                    "ERROR",
                    "NONSTANDARD_TERM",
                    f"检测到已确认专名的错写“{wrong}”，规范写法为“{canonical}”；请全文替换并复查标题、导语、正文和 CTA。",
                    str(path),
                )
            )
    return findings


def completed_coverage_map(path: Path) -> bool:
    """Require enough populated rows that an empty template cannot lift a score cap."""
    if not path.is_file():
        return False
    rows = 0
    has_a_level = False
    for line in read_text(path).splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 9 or cells[0] in {"时间段", ""} or set(cells[0]) <= {"-", ":"}:
            continue
        level = next((cell for cell in cells if cell in {"A", "B", "C"}), "")
        if not level:
            continue
        rows += 1
        has_a_level = has_a_level or level == "A"
    return rows >= 8 and has_a_level


def audit_coverage_map(text: str, source: str, path: Path) -> list[Finding]:
    """Validate that the coverage table spans the interview and has usable decisions."""
    findings: list[Finding] = []
    if len(source_interview_content(source)) >= MIN_LONG_INTERVIEW_CHARS:
        if "## 导语设计卡" not in text:
            findings.append(
                Finding(
                    "WARNING",
                    "MISSING_INTRO_DESIGN_CARD",
                    "长访谈覆盖表缺少导语设计卡，无法复核场景关系、主持人感受、全文问题和第一章落点。",
                    str(path),
                )
            )
        if "## 结尾收束卡" not in text:
            findings.append(
                Finding(
                    "WARNING",
                    "MISSING_ENDING_DESIGN_CARD",
                    "长访谈覆盖表缺少结尾收束卡，无法证明最终总结题之前的低价值转场已逐轮复查。",
                    str(path),
                )
            )
    rows: list[tuple[int, int, list[str]]] = []
    source_times = [timestamp for _, timestamp in SOURCE_SPEAKER_RE.findall(source)]
    source_start = time_to_seconds(source_times[0]) if source_times else 0
    source_end = time_to_seconds(source_times[-1]) if source_times else 0
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 9 or cells[0] in {"时间段", ""} or set(cells[0]) <= {"-", ":"}:
            continue
        timestamps = TIMESTAMP_RE.findall(cells[0])
        if len(timestamps) != 2:
            findings.append(
                Finding("ERROR", "COVERAGE_TIMECODE", f"覆盖表时间段无法识别起止时间：{cells[0]}", str(path))
            )
            continue
        start, end = timestamps
        start_seconds, end_seconds = time_to_seconds(start), time_to_seconds(end)
        if source_times and (start_seconds < max(0, source_start - 120) or end_seconds > source_end + 120):
            findings.append(
                Finding(
                    "ERROR",
                    "COVERAGE_SOURCE_TIMECODE",
                    f"覆盖表时间段 {start}–{end} 超出原稿有效时间范围。",
                    str(path),
                )
            )
        if start_seconds > end_seconds:
            findings.append(
                Finding("ERROR", "COVERAGE_ORDER", f"覆盖表时间段倒置：{start}–{end}。", str(path))
            )
        if any(not cell or cell in {"—", "待填写", "待补充"} for cell in cells[1:7]):
            findings.append(
                Finding(
                    "ERROR",
                    "COVERAGE_EMPTY_DECISION",
                    f"覆盖表 {start}–{end} 缺少话题、细节、主持人、嘉宾原声、级别或处理决定。",
                    str(path),
                )
            )
        if cells[5] not in {"A", "B", "C"}:
            findings.append(
                Finding("ERROR", "COVERAGE_LEVEL", f"覆盖表 {start}–{end} 的价值级别无效：{cells[5]}", str(path))
            )
        rows.append((start_seconds, end_seconds, cells))

    if len(rows) < 8 or not any(cells[5] == "A" for _, _, cells in rows):
        findings.append(
            Finding(
                "ERROR",
                "INCOMPLETE_COVERAGE_MAP",
                "内容覆盖表少于 8 个有效追问链/语义模块或没有 A 级模块。",
                str(path),
            )
        )
        return findings

    for previous, current in zip(rows, rows[1:]):
        if current[0] < previous[0]:
            findings.append(
                Finding("ERROR", "COVERAGE_ROW_ORDER", "内容覆盖表没有按时间顺序排列。", str(path))
            )
            break
        if current[0] - previous[1] > 15 * 60:
            findings.append(
                Finding(
                    "ERROR",
                    "COVERAGE_GAP",
                    f"内容覆盖表在 {previous[2][0]} 与 {current[2][0]} 之间存在超过15分钟的未解释空档。",
                    str(path),
                )
            )
    if source_times:
        if rows[0][0] - time_to_seconds(source_times[0]) > 10 * 60:
            findings.append(Finding("ERROR", "COVERAGE_START", "内容覆盖表遗漏开场超过10分钟。", str(path)))
        if time_to_seconds(source_times[-1]) - rows[-1][1] > 10 * 60:
            findings.append(Finding("ERROR", "COVERAGE_END", "内容覆盖表遗漏结尾超过10分钟。", str(path)))
    semantic_end_match = SEMANTIC_END_RE.search(text)
    if semantic_end_match:
        semantic_end_text = semantic_end_match.group(1)
        semantic_end = time_to_seconds(semantic_end_text)
        if semantic_end_text not in source_times:
            findings.append(
                Finding(
                    "ERROR",
                    "SEMANTIC_END_NOT_IN_SOURCE",
                    f"发布稿语义结束点 {semantic_end_text} 不是原稿中的发言块时间码。",
                    str(path),
                )
            )
        if source_times and source_end - semantic_end > 5 * 60:
            findings.append(
                Finding(
                    "ERROR",
                    "SEMANTIC_END_TOO_EARLY",
                    f"发布稿语义结束点距离物理结尾超过5分钟（{semantic_end_text}–{source_times[-1]}）；"
                    "不能借语义截止大段排除有效问答，须逐模块说明取舍。",
                    str(path),
                )
            )
        tail_rows = [cells for start, _, cells in rows if start >= semantic_end]
        if not tail_rows or any(
            cells[5] != "C" or not re.search(r"删除|不进发布稿", cells[6] + cells[7] + cells[8])
            for cells in tail_rows
        ):
            findings.append(
                Finding(
                    "ERROR",
                    "SEMANTIC_END_WITHOUT_C_TAIL",
                    "发布稿语义结束点之后必须由覆盖表逐段标成C级删除，并说明是截图、摆位或物理关播尾声。",
                    str(path),
                )
            )
    return findings


def audit_edit_ledger(text: str, source: str, issues: str, draft: str, path: Path, *, speaker_analysis: SpeakerAnalysis | None = None) -> list[Finding]:
    """Validate evidence for every non-trivial speaker, ASR, fact, or privacy edit."""
    findings: list[Finding] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [Finding("ERROR", "EDIT_LEDGER_JSON", f"编辑决策台账 JSON 无效：{exc}", str(path))]
    if data.get("version") != 1:
        findings.append(Finding("ERROR", "EDIT_LEDGER_VERSION", "编辑决策台账 version 必须为 1。", str(path)))
    if data.get("source") != f"inputs/{SOURCE_NAME}":
        findings.append(
            Finding("ERROR", "EDIT_LEDGER_SOURCE", f"编辑决策台账 source 必须为 inputs/{SOURCE_NAME}。", str(path))
        )
    speaker_map = data.get("speaker_map")
    map_valid = (speaker_analysis.valid_ledger_map(speaker_map) if speaker_analysis is not None
                 else isinstance(speaker_map, dict) and len(speaker_map) == 2 and all(speaker_map.values()))
    if not map_valid:
        findings.append(
            Finding("ERROR", "EDIT_LEDGER_SPEAKERS", "台账标签映射必须与本场已核身份一致；未知标签保留null，并由逐块时间轴提供归属。", str(path))
        )
    edits = data.get("nontrivial_edits")
    if not isinstance(edits, list):
        return findings + [Finding("ERROR", "EDIT_LEDGER_ROWS", "nontrivial_edits 必须为数组。", str(path))]
    seen_ids: set[str] = set()
    recorded_draft_excerpts: list[str] = []
    source_times = set(TIMESTAMP_RE.findall(source))
    for index, edit in enumerate(edits, start=1):
        if not isinstance(edit, dict):
            findings.append(Finding("ERROR", "EDIT_LEDGER_ROW", f"第 {index} 条编辑记录不是对象。", str(path)))
            continue
        edit_id = str(edit.get("id", "")).strip()
        if not edit_id or edit_id in seen_ids:
            findings.append(Finding("ERROR", "EDIT_LEDGER_ID", f"第 {index} 条编辑记录 ID 缺失或重复。", str(path)))
        seen_ids.add(edit_id)
        start, end = edit.get("start"), edit.get("end")
        if start not in source_times or end not in source_times:
            findings.append(
                Finding("ERROR", "EDIT_LEDGER_TIMECODE", f"{edit_id or index} 的时间码 {start}–{end} 未对应原稿。", str(path))
            )
        elif time_to_seconds(str(start)) > time_to_seconds(str(end)):
            findings.append(Finding("ERROR", "EDIT_LEDGER_ORDER", f"{edit_id or index} 的时间码顺序错误。", str(path)))
        edit_type = edit.get("type")
        basis = edit.get("basis")
        confidence = edit.get("confidence")
        status = edit.get("status")
        if edit_type not in ALLOWED_EDIT_TYPES:
            findings.append(Finding("ERROR", "EDIT_LEDGER_TYPE", f"{edit_id or index} 的编辑类型无效：{edit_type}", str(path)))
        if basis not in ALLOWED_EDIT_BASES:
            findings.append(Finding("ERROR", "EDIT_LEDGER_BASIS", f"{edit_id or index} 的核验依据无效：{basis}", str(path)))
        if confidence not in {"已确认", "暂定"}:
            findings.append(Finding("ERROR", "EDIT_LEDGER_CONFIDENCE", f"{edit_id or index} 的置信状态无效：{confidence}", str(path)))
        if status not in ALLOWED_ISSUE_STATUSES:
            findings.append(Finding("ERROR", "EDIT_LEDGER_STATUS", f"{edit_id or index} 的处理状态无效：{status}", str(path)))
        source_excerpt = normalize_text(str(edit.get("source_excerpt", "")))
        draft_excerpt = normalize_text(str(edit.get("draft_excerpt", "")))
        if draft_excerpt:
            recorded_draft_excerpts.append(draft_excerpt)
        if not source_excerpt or source_excerpt not in normalize_text(source):
            findings.append(Finding("ERROR", "EDIT_LEDGER_SOURCE_EXCERPT", f"{edit_id or index} 的原文摘录未在原稿中找到。", str(path)))
        if status not in {"已删除"} and (not draft_excerpt or draft_excerpt not in normalize_text(draft)):
            findings.append(Finding("ERROR", "EDIT_LEDGER_DRAFT_EXCERPT", f"{edit_id or index} 的成稿摘录未在发布稿中找到。", str(path)))
        if edit_type == "说话人纠正" and confidence == "已确认" and basis not in {"回放音频", "回放画面"}:
            findings.append(
                Finding("ERROR", "SPEAKER_EDIT_WITHOUT_REPLAY", f"{edit_id or index} 的说话人纠正没有回放证据。", str(path))
            )
        if edit_type == "ASR语义修复" and confidence == "已确认" and basis != "回放音频":
            findings.append(
                Finding("ERROR", "ASR_EDIT_WITHOUT_AUDIO", f"{edit_id or index} 的ASR语义修复没有音频依据，不能标记已确认。", str(path))
            )
        issue_covers_start = False
        if isinstance(start, str):
            start_seconds = time_to_seconds(start)
            for issue_line in issues.splitlines():
                if not re.match(r"^\|\s*P[01]\s*\|", issue_line):
                    continue
                issue_cells = [cell.strip() for cell in issue_line.strip().strip("|").split("|")]
                issue_times = TIMESTAMP_RE.findall(issue_cells[1] if len(issue_cells) > 1 else "")
                for time_index in range(0, len(issue_times), 2):
                    range_start = time_to_seconds(issue_times[time_index])
                    range_end = (
                        time_to_seconds(issue_times[time_index + 1])
                        if time_index + 1 < len(issue_times)
                        else range_start
                    )
                    if range_start <= start_seconds <= range_end:
                        issue_covers_start = True
                        break
                if issue_covers_start:
                    break
        if confidence == "暂定" and not issue_covers_start:
            findings.append(
                Finding("ERROR", "PROVISIONAL_EDIT_NOT_LISTED", f"{edit_id or index} 是暂定编辑，但待核清单中没有对应时间码。", str(path))
            )
    for note in re.findall(r"\[编辑说明[：:](.*?)\]", draft):
        normalized_note = normalize_text(note)
        if not normalized_note:
            continue
        if not any(
            excerpt in normalized_note or normalized_note in excerpt
            for excerpt in recorded_draft_excerpts
        ):
            findings.append(
                Finding(
                    "ERROR",
                    "UNTRACKED_EDITOR_NOTE",
                    f"正文编辑说明没有对应的编辑决策台账：{note[:100]}",
                    str(path),
                )
            )
    return findings


def parse_speaker_conflict_rows(text: str, path: Path) -> tuple[dict[str, dict[str, object]], list[Finding]]:
    findings: list[Finding] = []
    rows: dict[str, dict[str, object]] = {}
    weak_closures: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not re.match(r"^\|\s*C\d+\s*\|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 9:
            findings.append(
                Finding(
                    "ERROR",
                    "SPEAKER_CONFLICT_ROW",
                    f"说话人冲突清单第{line_number}行应有9列，当前为{len(cells)}列。",
                    str(path),
                )
            )
            continue
        conflict_id, level, timecode, conflict_type, source_label, timeline, context, decision, status = cells
        if conflict_id in rows:
            findings.append(
                Finding(
                    "ERROR",
                    "SPEAKER_CONFLICT_ID",
                    f"说话人冲突编号重复：{conflict_id}。",
                    str(path),
                )
            )
            continue
        times = TIMESTAMP_RE.findall(timecode)
        if not times:
            findings.append(
                Finding(
                    "ERROR",
                    "SPEAKER_CONFLICT_TIMECODE",
                    f"{conflict_id} 缺少有效时间码。",
                    str(path),
                )
            )
            second = None
        else:
            second = time_to_seconds(times[0])
        if level not in {"P0", "P1"}:
            findings.append(
                Finding(
                    "ERROR",
                    "SPEAKER_CONFLICT_LEVEL",
                    f"{conflict_id} 级别必须为P0或P1，当前为“{level}”。",
                    str(path),
                )
            )
        if status not in ALLOWED_ISSUE_STATUSES:
            findings.append(
                Finding(
                    "ERROR",
                    "SPEAKER_CONFLICT_STATUS",
                    f"{conflict_id} 状态无效：{status}。",
                    str(path),
                )
            )
        if status == "已解决":
            has_positive_evidence = any(
                token in decision
                for token in (
                    "回放音频",
                    "回放画面",
                    "逐帧",
                    "声纹检测",
                    "声纹窗口确认",
                    "已回听",
                    "当事人确认",
                    "真人确认",
                )
            )
            explicitly_insufficient = any(
                token in decision
                for token in ("声纹不足", "无法稳定归属", "无法映射", "低置信")
            )
            if not has_positive_evidence or explicitly_insufficient:
                weak_closures.append(conflict_id)
        if not conflict_type or not source_label or not timeline:
            findings.append(
                Finding(
                    "ERROR",
                    "SPEAKER_CONFLICT_DETAIL",
                    f"{conflict_id} 必须填写冲突类型、原转写标签和独立时间轴结论。",
                    str(path),
                )
            )
        rows[conflict_id] = {
            "level": level,
            "second": second,
            "status": status,
            "decision": decision,
            "context": context,
        }
    if weak_closures:
        findings.append(
            Finding(
                "WARNING",
                "SPEAKER_CONFLICT_WEAK_CLOSURE",
                f"{len(weak_closures)}项说话人冲突仅靠上下文或问答逻辑标记为‘已解决’，"
                "不能当作声音身份结论："
                + "、".join(weak_closures[:10]),
                str(path),
            )
        )
    return rows, findings


def issue_list_covers_second(issues: str, second: int) -> bool:
    for line in issues.splitlines():
        if not re.match(r"^\|\s*P[01]\s*\|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        times = TIMESTAMP_RE.findall(cells[1] if len(cells) > 1 else "")
        for index in range(0, len(times), 2):
            start = time_to_seconds(times[index])
            end = time_to_seconds(times[index + 1]) if index + 1 < len(times) else start
            if start <= second <= end:
                return True
    return False


def audit_speaker_preflight(
    source: str,
    issues: str,
    calibration_text: str,
    timeline_text: str,
    conflicts_text: str,
    calibration_path: Path,
    timeline_path: Path,
    conflicts_path: Path,
) -> list[Finding]:
    """Validate speaker identity anchors, the independent timeline, and all conflicts."""
    findings: list[Finding] = []
    try:
        calibration = json.loads(calibration_text)
    except json.JSONDecodeError as exc:
        return [
            Finding(
                "ERROR",
                "SPEAKER_CALIBRATION_JSON",
                f"说话人校准 JSON 无效：{exc}",
                str(calibration_path),
            )
        ]
    try:
        timeline = json.loads(timeline_text)
    except json.JSONDecodeError as exc:
        return [
            Finding(
                "ERROR",
                "SPEAKER_TIMELINE_JSON",
                f"说话人时间轴 JSON 无效：{exc}",
                str(timeline_path),
            )
        ]

    source_matches = list(SOURCE_SPEAKER_RE.finditer(source))
    source_block_keys = [
        (time_to_seconds(match.group(2)), f"发言人{match.group(1)}")
        for match in source_matches
    ]
    source_labels = {label for _, label in source_block_keys}

    if calibration.get("version") != 1:
        findings.append(
            Finding("ERROR", "SPEAKER_CALIBRATION_VERSION", "说话人校准 version 必须为1。", str(calibration_path))
        )
    if calibration.get("source_audio") != replay_relative(calibration_path.resolve().parents[1]):
        findings.append(
            Finding(
                "ERROR",
                "SPEAKER_CALIBRATION_SOURCE",
                "说话人校准 source_audio 必须指向本场唯一原始回放。",
                str(calibration_path),
            )
        )
    if calibration.get("speaker_count") != 2:
        findings.append(
            Finding("ERROR", "SPEAKER_CALIBRATION_COUNT", "说话人校准必须固定为两位主要说话人。", str(calibration_path))
        )
    method = str(calibration.get("method", "")).strip()
    if not method or "待填" in method:
        findings.append(
            Finding("ERROR", "SPEAKER_CALIBRATION_METHOD", "说话人校准必须记录实际采用的方法。", str(calibration_path))
        )
    if calibration.get("status") not in CONFIRMED_SPEAKER_REVIEW_STATUSES:
        findings.append(
            Finding(
                "ERROR",
                "SPEAKER_CALIBRATION_UNCONFIRMED",
                "两位说话人的身份锚点尚未确认，不能进入完整底稿编辑。",
                str(calibration_path),
            )
        )

    speakers = calibration.get("speakers")
    if not isinstance(speakers, list) or len(speakers) != 2:
        return findings + [
            Finding(
                "ERROR",
                "SPEAKER_CALIBRATION_SPEAKERS",
                "说话人校准必须包含两位说话人的独立身份记录。",
                str(calibration_path),
            )
        ]

    speaker_ids: set[str] = set()
    speaker_names: set[str] = set()
    roles: set[str] = set()
    all_reference_ranges: list[tuple[int, int, str]] = []
    for index, speaker in enumerate(speakers, start=1):
        if not isinstance(speaker, dict):
            findings.append(
                Finding("ERROR", "SPEAKER_CALIBRATION_ROW", f"第{index}位说话人记录不是对象。", str(calibration_path))
            )
            continue
        speaker_id = str(speaker.get("speaker_id", "")).strip()
        name = str(speaker.get("name", "")).strip()
        role = str(speaker.get("role", "")).strip()
        if not speaker_id or speaker_id in speaker_ids:
            findings.append(
                Finding("ERROR", "SPEAKER_CALIBRATION_ID", f"第{index}位说话人的 speaker_id 缺失或重复。", str(calibration_path))
            )
        if not name or "姓名" in name or name in speaker_names:
            findings.append(
                Finding("ERROR", "SPEAKER_CALIBRATION_NAME", f"第{index}位说话人的真实姓名缺失、仍是占位或重复。", str(calibration_path))
            )
        if role not in {"主持人", "嘉宾"}:
            findings.append(
                Finding("ERROR", "SPEAKER_CALIBRATION_ROLE", f"{speaker_id or index} 的角色必须为主持人或嘉宾。", str(calibration_path))
            )
        speaker_ids.add(speaker_id)
        speaker_names.add(name)
        roles.add(role)

        references = speaker.get("reference_segments")
        if not isinstance(references, list) or len(references) < 2:
            findings.append(
                Finding(
                    "ERROR",
                    "SPEAKER_CALIBRATION_REFERENCES",
                    f"{speaker_id or index} 至少需要两段独立声音锚点。",
                    str(calibration_path),
                )
            )
            continue
        total_duration = 0
        for ref_index, reference in enumerate(references, start=1):
            if not isinstance(reference, dict):
                findings.append(
                    Finding("ERROR", "SPEAKER_CALIBRATION_REFERENCE_ROW", f"{speaker_id} 第{ref_index}段锚点不是对象。", str(calibration_path))
                )
                continue
            start = str(reference.get("start", ""))
            end = str(reference.get("end", ""))
            if not TIMESTAMP_RE.fullmatch(start) or not TIMESTAMP_RE.fullmatch(end):
                findings.append(
                    Finding("ERROR", "SPEAKER_CALIBRATION_TIMECODE", f"{speaker_id} 第{ref_index}段锚点时间码无效。", str(calibration_path))
                )
                continue
            start_second = time_to_seconds(start)
            end_second = time_to_seconds(end)
            if start_second >= end_second:
                findings.append(
                    Finding("ERROR", "SPEAKER_CALIBRATION_ORDER", f"{speaker_id} 第{ref_index}段锚点起止顺序错误。", str(calibration_path))
                )
                continue
            duration = end_second - start_second
            total_duration += duration
            all_reference_ranges.append((start_second, end_second, speaker_id))
            basis = str(reference.get("identity_basis", "")).strip()
            if (
                not basis
                or "填写" in basis
                or any(token in basis for token in ("原转写标签", "内容语气", "上下文猜"))
            ):
                findings.append(
                    Finding(
                        "ERROR",
                        "SPEAKER_CALIBRATION_BASIS",
                        f"{speaker_id} 第{ref_index}段没有独立、可复核的身份依据。",
                        str(calibration_path),
                    )
                )
            if reference.get("overlap") is not False:
                findings.append(
                    Finding("ERROR", "SPEAKER_CALIBRATION_OVERLAP", f"{speaker_id} 第{ref_index}段锚点含重叠讲话。", str(calibration_path))
                )
            if reference.get("review_status") not in CONFIRMED_SPEAKER_REVIEW_STATUSES:
                findings.append(
                    Finding("ERROR", "SPEAKER_CALIBRATION_REFERENCE_UNCONFIRMED", f"{speaker_id} 第{ref_index}段锚点尚未确认。", str(calibration_path))
                )
            if duration < 10 or duration > 30:
                findings.append(
                    Finding(
                        "WARNING",
                        "SPEAKER_CALIBRATION_REFERENCE_LENGTH",
                        f"{speaker_id} 第{ref_index}段为{duration}秒；建议使用10–30秒干净语音。",
                        str(calibration_path),
                    )
                )
        if total_duration < 30:
            findings.append(
                Finding(
                    "ERROR",
                    "SPEAKER_CALIBRATION_DURATION",
                    f"{speaker_id or index} 的身份锚点累计只有{total_duration}秒，至少需要30秒。",
                    str(calibration_path),
                )
            )

    for index, (start, end, speaker_id) in enumerate(all_reference_ranges):
        for other_start, other_end, other_id in all_reference_ranges[index + 1 :]:
            if speaker_id != other_id and start < other_end and other_start < end:
                findings.append(
                    Finding(
                        "ERROR",
                        "SPEAKER_CALIBRATION_CROSS_OVERLAP",
                        f"{speaker_id}与{other_id}的身份锚点在时间线上重叠，不能作为干净样本。",
                        str(calibration_path),
                    )
                )

    default_map = calibration.get("source_label_default_map")
    if not valid_default_map(default_map, speaker_ids, source_labels):
        findings.append(
            Finding(
                "ERROR",
                "SPEAKER_CALIBRATION_DEFAULT_MAP",
                "默认映射须覆盖原稿标签及已校准身份；未标注字幕必须为 null，并逐块回听核对。",
                str(calibration_path),
            )
        )

    if timeline.get("version") != 1:
        findings.append(Finding("ERROR", "SPEAKER_TIMELINE_VERSION", "说话人时间轴 version 必须为1。", str(timeline_path)))
    if timeline.get("source_audio") != replay_relative(timeline_path.resolve().parents[1]):
        findings.append(Finding("ERROR", "SPEAKER_TIMELINE_AUDIO", "说话人时间轴 source_audio 路径无效。", str(timeline_path)))
    if timeline.get("source_transcript") != f"inputs/{SOURCE_NAME}":
        findings.append(Finding("ERROR", "SPEAKER_TIMELINE_SOURCE", f"说话人时间轴必须指向 inputs/{SOURCE_NAME}。", str(timeline_path)))
    if timeline.get("speaker_count") != 2:
        findings.append(Finding("ERROR", "SPEAKER_TIMELINE_COUNT", "说话人时间轴必须固定为两位主要说话人。", str(timeline_path)))
    if timeline.get("calibration_file") != f"_work/{SPEAKER_CALIBRATION_NAME}":
        findings.append(Finding("ERROR", "SPEAKER_TIMELINE_CALIBRATION", "说话人时间轴未绑定当前校准文件。", str(timeline_path)))
    timeline_method = str(timeline.get("method", "")).strip()
    if not timeline_method or "填写" in timeline_method:
        findings.append(Finding("ERROR", "SPEAKER_TIMELINE_METHOD", "说话人时间轴必须记录实际工具和版本。", str(timeline_path)))

    segments = timeline.get("segments")
    segment_rows: dict[str, dict[str, object]] = {}
    if not isinstance(segments, list) or not segments:
        findings.append(Finding("ERROR", "SPEAKER_TIMELINE_SEGMENTS", "说话人时间轴没有有效片段。", str(timeline_path)))
        segments = []
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_SEGMENT_ROW", f"时间轴第{index}段不是对象。", str(timeline_path)))
            continue
        segment_id = str(segment.get("id", "")).strip()
        if not segment_id or segment_id in segment_rows:
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_SEGMENT_ID", f"时间轴第{index}段ID缺失或重复。", str(timeline_path)))
            continue
        start = str(segment.get("start", ""))
        end = str(segment.get("end", ""))
        if not TIMESTAMP_RE.fullmatch(start) or not TIMESTAMP_RE.fullmatch(end):
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_SEGMENT_TIME", f"{segment_id} 时间码无效。", str(timeline_path)))
            continue
        start_second = time_to_seconds(start)
        end_second = time_to_seconds(end)
        if start_second >= end_second:
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_SEGMENT_ORDER", f"{segment_id} 起止顺序错误。", str(timeline_path)))
        speaker_id = str(segment.get("speaker_id", "")).strip()
        if speaker_id not in speaker_ids:
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_SEGMENT_SPEAKER", f"{segment_id} 未映射到已校准说话人。", str(timeline_path)))
        if not str(segment.get("raw_cluster", "")).strip():
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_CLUSTER", f"{segment_id} 缺少原始聚类编号。", str(timeline_path)))
        if not isinstance(segment.get("overlap"), bool):
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_OVERLAP_FLAG", f"{segment_id} 缺少布尔型 overlap 标记。", str(timeline_path)))
        try:
            precise_duration = float(segment.get('end_seconds', end_second)) - float(segment.get('start_seconds', start_second))
        except (ValueError, TypeError):
            precise_duration = -1
        segment_rows[segment_id] = {
            "start": start_second,
            "end": end_second,
            "speaker_id": speaker_id,
            "overlap": segment.get("overlap"),
            "uncertain": segment.get("uncertain"),
            "duration": precise_duration,
        }

    ordered_segments = sorted(segment_rows.items(), key=lambda item: (int(item[1]["start"]), int(item[1]["end"])))
    for index, (segment_id, segment) in enumerate(ordered_segments):
        for other_id, other in ordered_segments[index + 1 :]:
            if int(other["start"]) >= int(segment["end"]):
                break
            if (
                segment["speaker_id"] != other["speaker_id"]
                and not (segment["overlap"] is True and other["overlap"] is True)
            ):
                findings.append(
                    Finding(
                        "ERROR",
                        "SPEAKER_TIMELINE_UNMARKED_OVERLAP",
                        f"{segment_id}与{other_id}发生重叠，但没有同时标记为 overlap。",
                        str(timeline_path),
                    )
                )

    conflict_rows, conflict_findings = parse_speaker_conflict_rows(conflicts_text, conflicts_path)
    findings.extend(conflict_findings)

    source_blocks = timeline.get("source_blocks")
    from source_turns import CONTRACT,contract_state,timeline_identity_errors
    ordered,contract_errors=contract_state(timeline_path.resolve().parents[1])
    if ordered or timeline.get('transcript_contract')==CONTRACT:
        try:identity_errors=timeline_identity_errors(source,source_blocks)
        except (ValueError,TypeError,KeyError) as exc:identity_errors=[str(exc)]
        for error in contract_errors+identity_errors:
            findings.append(Finding('ERROR','SPEAKER_SOURCE_BLOCK_IDENTITY',error,str(timeline_path)))
    actual_block_keys: list[tuple[int, str]] = []
    referenced_conflicts: set[str] = set()
    open_conflicts: set[str] = set()
    if not isinstance(source_blocks, list) or not source_blocks:
        findings.append(Finding("ERROR", "SPEAKER_TIMELINE_SOURCE_BLOCKS", "说话人时间轴没有逐块对齐原稿。", str(timeline_path)))
        source_blocks = []
    if any(isinstance(block, dict) and block.get('status') == '声学归属' for block in source_blocks):
        try:
            case = timeline_path.resolve().parents[1]
            replay = find_replay(case)
            source_path = case / 'inputs' / SOURCE_NAME
            bound = timeline.get('provenance', {})
            if calibration.get('source_sha256') != file_sha256(replay) or read_text(source_path) != source:
                raise ValueError('回放身份锚点或原稿绑定不一致')
            for key, item in [('replay_sha256', replay), ('transcript_sha256', source_path), ('calibration_sha256', calibration_path)]:
                if bound.get(key) != file_sha256(item):
                    raise ValueError('当前输入绑定不一致：' + key)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            findings.append(Finding('ERROR', 'SPEAKER_ACOUSTIC_BINDING', '声学归属必须绑定当前回放、原稿和身份锚点：'+str(error), str(timeline_path)))
    for index, block in enumerate(source_blocks, start=1):
        if not isinstance(block, dict):
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_BLOCK_ROW", f"原稿对齐第{index}条不是对象。", str(timeline_path)))
            continue
        start = str(block.get("source_start", ""))
        source_label = str(block.get("source_label", "")).strip()
        if not TIMESTAMP_RE.fullmatch(start):
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_BLOCK_TIME", f"原稿对齐第{index}条时间码无效。", str(timeline_path)))
            continue
        start_second = time_to_seconds(start)
        actual_block_keys.append((start_second, source_label))
        status = str(block.get("status", "")).strip()
        if status not in ALLOWED_SPEAKER_BLOCK_STATUSES:
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_BLOCK_STATUS", f"{start} 的对齐状态无效：{status}。", str(timeline_path)))
        resolved_id = str(block.get("resolved_speaker_id") or "").strip()
        segment_ids = block.get("timeline_segment_ids")
        if not isinstance(segment_ids, list):
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_BLOCK_SEGMENTS", f"{start} 的 timeline_segment_ids 不是数组。", str(timeline_path)))
            segment_ids = []
        unknown_segment_ids = [str(item) for item in segment_ids if str(item) not in segment_rows]
        if unknown_segment_ids:
            findings.append(
                Finding(
                    "ERROR",
                    "SPEAKER_TIMELINE_BLOCK_SEGMENT_ID",
                    f"{start} 引用了不存在的时间轴片段：{unknown_segment_ids}。",
                    str(timeline_path),
                )
            )
        known_segments = [segment_rows[str(item)] for item in segment_ids if str(item) in segment_rows]
        if status != "无法映射" and not known_segments:
            findings.append(Finding("ERROR", "SPEAKER_TIMELINE_BLOCK_UNLINKED", f"{start} 未链接有效时间轴片段。", str(timeline_path)))
        if known_segments:
            earliest = min(int(item["start"]) for item in known_segments)
            latest = max(int(item["end"]) for item in known_segments)
            if not earliest - SPEAKER_ALIGNMENT_MAX_DRIFT_SECONDS <= start_second <= latest + SPEAKER_ALIGNMENT_MAX_DRIFT_SECONDS:
                findings.append(
                    Finding(
                        "ERROR",
                        "SPEAKER_TIMELINE_BLOCK_DRIFT",
                        f"{start} 与引用的时间轴片段偏移超过{SPEAKER_ALIGNMENT_MAX_DRIFT_SECONDS}秒。",
                        str(timeline_path),
                    )
                )
            elif not earliest - SPEAKER_ALIGNMENT_REVIEW_DRIFT_SECONDS <= start_second <= latest + SPEAKER_ALIGNMENT_REVIEW_DRIFT_SECONDS:
                findings.append(
                    Finding(
                        "WARNING",
                        "SPEAKER_TIMELINE_BLOCK_DRIFT_REVIEW",
                        f"{start} 与引用片段偏移超过{SPEAKER_ALIGNMENT_REVIEW_DRIFT_SECONDS}秒；请确认平台时间戳或切段边界。",
                        str(timeline_path),
                    )
                )
        if status == "混合说话人":
            linked_speakers = {str(item["speaker_id"]) for item in known_segments}
            if len(linked_speakers) < 2:
                findings.append(
                    Finding(
                        "ERROR",
                        "SPEAKER_TIMELINE_MIXED_EVIDENCE",
                        f"{start} 标为混合说话人，但没有链接两位说话人的片段。",
                        str(timeline_path),
                    )
                )
        elif status != "无法映射":
            if resolved_id not in speaker_ids:
                findings.append(Finding("ERROR", "SPEAKER_TIMELINE_BLOCK_SPEAKER", f"{start} 未给出有效归属。", str(timeline_path)))
            elif known_segments and resolved_id not in {str(item["speaker_id"]) for item in known_segments}:
                findings.append(
                    Finding(
                        "ERROR",
                        "SPEAKER_TIMELINE_BLOCK_EVIDENCE",
                        f"{start} 的归属与引用时间轴片段不一致。",
                        str(timeline_path),
                    )
                )
        conflict_id = str(block.get("conflict_id") or "").strip()
        if status == "一致":
            if conflict_id:
                findings.append(Finding("ERROR", "SPEAKER_TIMELINE_FALSE_CONFLICT", f"{start} 标为一致却填写了冲突编号。", str(timeline_path)))
            expected_id = str(default_map.get(source_label, "")) if isinstance(default_map, dict) else ""
            if resolved_id != expected_id:
                findings.append(
                    Finding(
                        "ERROR",
                        "SPEAKER_TIMELINE_HIDDEN_CONFLICT",
                        f"{start} 的原标签默认映射为{expected_id or '未知'}，实际归属为{resolved_id or '未知'}，不能标为一致。",
                        str(timeline_path),
                    )
                )
        elif status == '声学归属':
            if conflict_id or source_label != '发言人未标注' or not isinstance(default_map, dict) or default_map.get(source_label) is not None:
                findings.append(Finding('ERROR', 'SPEAKER_ACOUSTIC_LABEL', f'{start} 声学归属只用于原标签未知且没有既存标签假设的块。', str(timeline_path)))
            if not known_segments or {item['speaker_id'] for item in known_segments} != {resolved_id} or any(
                item.get('uncertain') is not False or item.get('overlap') is not False
                or not math.isfinite(item['duration']) or item['duration'] < 3 for item in known_segments
            ):
                findings.append(Finding('ERROR', 'SPEAKER_ACOUSTIC_UNCERTAIN', f'{start} 含混合、重叠、短片段或不确定声音，必须进入待核冲突。', str(timeline_path)))
        else:
            if not conflict_id:
                findings.append(
                    Finding(
                        "ERROR",
                        "SPEAKER_TIMELINE_CONFLICT_ID",
                        f"{start} 的非一致状态缺少冲突编号。",
                        str(timeline_path),
                    )
                )
            elif conflict_id not in conflict_rows:
                findings.append(
                    Finding(
                        "ERROR",
                        "SPEAKER_TIMELINE_CONFLICT_MISSING",
                        f"{start} 引用的冲突编号 {conflict_id} 不在冲突清单。",
                        str(conflicts_path),
                    )
                )
            else:
                referenced_conflicts.add(conflict_id)
                row = conflict_rows[conflict_id]
                row_second = row.get("second")
                if isinstance(row_second, int) and abs(row_second - start_second) > 3:
                    findings.append(
                        Finding(
                            "ERROR",
                            "SPEAKER_TIMELINE_CONFLICT_DRIFT",
                            f"{conflict_id} 与原稿块时间偏移超过3秒。",
                            str(conflicts_path),
                        )
                    )
                if row.get("status") == "待确认":
                    open_conflicts.add(conflict_id)
                    if not issue_list_covers_second(issues, start_second):
                        findings.append(
                            Finding(
                                "ERROR",
                                "SPEAKER_CONFLICT_NOT_IN_ISSUES",
                                f"{conflict_id} 尚未解决，但待核问题清单没有覆盖 {start}。",
                                str(conflicts_path),
                            )
                        )
                elif status == "真人已确认" and row.get("status") not in CONFIRMED_SPEAKER_REVIEW_STATUSES:
                    findings.append(
                        Finding(
                            "ERROR",
                            "SPEAKER_CONFLICT_CONFIRMATION_DRIFT",
                            f"{conflict_id} 标为真人已确认，但冲突清单状态为“{row.get('status')}”。",
                            str(conflicts_path),
                        )
                    )

    expected_counter = Counter(source_block_keys)
    actual_counter = Counter(actual_block_keys)
    if expected_counter != actual_counter:
        missing = list((expected_counter - actual_counter).elements())[:5]
        extra = list((actual_counter - expected_counter).elements())[:5]
        detail = []
        if missing:
            detail.append("漏块：" + "、".join(f"{format_timecode(second)} {label}" for second, label in missing))
        if extra:
            detail.append("多余或重复：" + "、".join(f"{format_timecode(second)} {label}" for second, label in extra))
        findings.append(
            Finding(
                "ERROR",
                "SPEAKER_TIMELINE_SOURCE_COVERAGE",
                "说话人时间轴必须逐块且仅一次覆盖原稿；" + "；".join(detail),
                str(timeline_path),
            )
        )

    stale_conflicts = sorted(set(conflict_rows) - referenced_conflicts)
    if stale_conflicts:
        findings.append(
            Finding(
                "ERROR",
                "SPEAKER_CONFLICT_STALE",
                f"说话人冲突清单存在未被时间轴引用的编号：{stale_conflicts[:5]}。",
                str(conflicts_path),
            )
        )
    if open_conflicts:
        findings.append(
            Finding(
                "WARNING",
                "SPEAKER_CONFLICT_OPEN",
                f"仍有{len(open_conflicts)}项说话人冲突待回听；稿件只能标记为待人工终审。",
                str(conflicts_path),
            )
        )
    return findings


def check_required_files(case_dir: Path) -> tuple[dict[str, Path], list[Finding]]:
    skill_root = Path(__file__).resolve().parents[1]
    paths = {
        "source": case_dir / "inputs" / SOURCE_NAME,
        "replay": case_dir / replay_relative(case_dir),
        "terms": case_dir / "inputs" / "03_人物与专名表.md",
        "background": case_dir / "inputs" / "04_背景及运营资料.md",
        "global_terms": skill_root / "references" / "proper-nouns.md",
        "draft": case_dir / "outputs" / DRAFT_NAME,
        "issues": case_dir / "outputs" / ISSUES_NAME,
        "report": case_dir / "outputs" / REPORT_NAME,
        "evidence": case_dir / "_work" / EVIDENCE_NAME,
        "coverage": case_dir / "_work" / COVERAGE_NAME,
        "full_draft": case_dir / "_work" / FULL_DRAFT_NAME,
        "edit_ledger": case_dir / "_work" / EDIT_LEDGER_NAME,
        "review_sheet": case_dir / "_work" / REVIEW_SHEET_NAME,
        "speaker_calibration": case_dir / "_work" / SPEAKER_CALIBRATION_NAME,
        "speaker_timeline": case_dir / "_work" / SPEAKER_TIMELINE_NAME,
        "speaker_conflicts": case_dir / "_work" / SPEAKER_CONFLICTS_NAME,
    }
    findings: list[Finding] = []
    for key in ("source", "draft", "issues", "report", "evidence"):
        if not paths[key].is_file():
            findings.append(
                Finding("ERROR", "MISSING_FILE", f"缺少必需文件：{paths[key].relative_to(case_dir)}")
            )
    if not paths["replay"].is_file():
        findings.append(
            Finding(
                "ERROR",
                "MISSING_REPLAY",
                "缺少本流程必需的直播回放；请先补齐素材，不能把文本预处理作为整场整理完成。",
            )
        )
    if not paths["terms"].is_file():
        findings.append(
            Finding(
                "WARNING",
                "MISSING_TERM_SHEET",
                "缺少本场 inputs/03_人物与专名表.md；请先建立姓名、品牌、产品和术语的规范写法与核验依据。",
            )
        )
    if not paths["global_terms"].is_file():
        findings.append(
            Finding("ERROR", "MISSING_TERM_LIBRARY", "缺少项目级专名规范库。", str(paths["global_terms"]))
        )
    if not paths["coverage"].is_file():
        findings.append(
            Finding(
                "ERROR",
                "MISSING_COVERAGE_MAP",
                f"缺少 _work/{COVERAGE_NAME}；原声沉浸稿不能证明每条追问链和删除内容已有去向。",
            )
        )
    if not paths["full_draft"].is_file():
        findings.append(
            Finding(
                "ERROR",
                "MISSING_FULL_DRAFT",
                f"缺少 _work/{FULL_DRAFT_NAME}；无法证明稿件按原时间顺序完整校订。",
            )
        )
    if not paths["edit_ledger"].is_file():
        findings.append(
            Finding(
                "ERROR",
                "MISSING_EDIT_LEDGER",
                f"缺少 _work/{EDIT_LEDGER_NAME}；无法区分普通校订、说话人纠正和低置信度推定。",
            )
        )
    speaker_package = ("speaker_calibration", "speaker_timeline", "speaker_conflicts")
    present_speaker_files = [key for key in speaker_package if paths[key].is_file()]
    if paths["replay"].is_file() or present_speaker_files:
        for key in speaker_package:
            if not paths[key].is_file():
                findings.append(
                    Finding(
                        "ERROR",
                        "MISSING_SPEAKER_PREFLIGHT",
                        f"缺少 {paths[key].relative_to(case_dir)}；有回放的任务必须在编辑前完成说话人预检包。",
                    )
                )
    else:
        findings.append(
            Finding(
                "WARNING",
                "SPEAKER_PREFLIGHT_UNAVAILABLE",
                "没有回放，无法建立独立说话人时间轴；本流程暂停在输入检查，不生成冒充完整整理的暂定稿。",
            )
        )
    return paths, findings


def audit_source(source: str, path: Path) -> list[Finding]:
    findings: list[Finding] = []
    speakers = set(SOURCE_SPEAKER_RE.findall(source))
    speaker_ids = {speaker for speaker, _ in speakers}
    if not speaker_ids:
        speaker_ids = set(NAMED_SPEAKER_RE.findall(source))
    if len(speaker_ids) != 2:
        findings.append(
            Finding(
                "ERROR",
                "SOURCE_SPEAKERS",
                f"原稿应有且仅有两位主要说话人，当前识别到 {len(speaker_ids)} 位：{sorted(speaker_ids)}",
                str(path),
            )
        )
    # Use timestamps attached to speaker labels. Header metadata may contain a
    # clock time (for example the export time ``23:37``) that is not part of the
    # interview timeline and would otherwise create a false backwards jump.
    timestamps = [timestamp for _, timestamp in SOURCE_SPEAKER_RE.findall(source)]
    if not timestamps:
        timestamps = TIMESTAMP_RE.findall(source)
    if not timestamps:
        findings.append(Finding("ERROR", "NO_TIMECODES", "原稿中未识别到时间码。", str(path)))
    else:
        seconds = [time_to_seconds(value) for value in timestamps]
        backwards = sum(current < previous for previous, current in zip(seconds, seconds[1:]))
        if backwards:
            findings.append(
                Finding(
                    "ERROR",
                    "TIMECODE_ORDER",
                    f"原稿时间码有 {backwards} 处倒退，需先确认文本顺序。",
                    str(path),
                )
            )
    return findings


def audit_draft(
    draft: str,
    support: str,
    path: Path,
    *,
    grounding_source: str | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    h1 = re.findall(r"^# (?!#).+", draft, re.MULTILINE)
    h2 = re.findall(r"^## .+", draft, re.MULTILINE)
    if len(h1) != 1:
        findings.append(Finding("ERROR", "H1_COUNT", f"发布稿应有 1 个 H1，当前为 {len(h1)}。", str(path)))
    if not h2:
        findings.append(Finding("ERROR", "NO_SECTIONS", "发布稿至少需要 1 个 H2 内容章节。", str(path)))

    transcript_marker = re.search(r"^\*\*以下文字版[：:]\*\*\s*$", draft, re.MULTILINE)
    if not transcript_marker:
        findings.append(
            Finding(
                "ERROR",
                "MISSING_TRANSCRIPT_MARKER",
                "发布稿缺少“以下文字版”分隔，无法区分直播导语和原声对谈正文。",
                str(path),
            )
        )
    else:
        introduction = draft[: transcript_marker.start()]
        if "直播" not in introduction:
            findings.append(
                Finding(
                    "ERROR",
                    "MISSING_LIVE_CONTEXT",
                    "正文前导语没有明确交代这是一场直播或直播对谈。",
                    str(path),
                )
            )
        # Neutral narration is the default when the publishing identity is unknown.
        # First-person authorization is an editorial input, not a required phrase.
        if not any(phrase in introduction for phrase in ("逐字", "感兴趣", "选择", "跳读")):
            findings.append(
                Finding(
                    "WARNING",
                    "MISSING_READING_GUIDE",
                    "长文导语未说明可逐字阅读或按感兴趣的问题选择阅读。",
                    str(path),
                )
            )
        internal_jargon = INTRO_INTERNAL_JARGON_RE.search(introduction)
        if internal_jargon:
            findings.append(
                Finding(
                    "ERROR",
                    "INTRO_INTERNAL_PROCESS_JARGON",
                    f"读者导语暴露内部整理流程“{internal_jargon.group()}”；应按已核发布身份自然说明对谈背景和阅读方式，不补造主持人感受。",
                    str(path),
                )
            )
        first_section = re.search(r"^##\s+", draft[transcript_marker.end() :], re.MULTILINE)
        opening_end = (
            transcript_marker.end() + first_section.start()
            if first_section
            else len(draft)
        )
        opening_turns = sum(
            1
            for match in DRAFT_TURN_RE.finditer(draft[transcript_marker.end() : opening_end])
            if match.group(1) not in NON_SPEAKER_LABELS
        )
        if opening_turns < 2:
            findings.append(
                Finding(
                    "ERROR",
                    "MISSING_NATURAL_OPENING",
                    "“以下文字版”后、第一章节前至少保留一组建立人物或引出主题的自然开场问答。",
                    str(path),
                )
            )

    speakers = set(NAMED_SPEAKER_RE.findall(draft)) - NON_SPEAKER_LABELS
    if len(speakers) != 2:
        findings.append(
            Finding(
                "ERROR",
                "DRAFT_SPEAKERS",
                f"发布稿应有且仅有两位说话人，当前识别到 {len(speakers)} 位：{sorted(speakers)}",
                str(path),
            )
        )
    if re.search(r"^发言人\s*\d+", draft, re.MULTILINE):
        findings.append(Finding("ERROR", "RAW_SPEAKER_LABEL", "发布稿仍含「发言人N」原始标签。", str(path)))

    named_matches = [
        match for match in NAMED_SPEAKER_RE.finditer(draft) if match.group(1) not in NON_SPEAKER_LABELS
    ]
    redundant_labels: list[str] = []
    for previous, current in zip(named_matches, named_matches[1:]):
        if previous.group(1) != current.group(1):
            continue
        between = draft[previous.end() : current.start()]
        if re.search(r"^##\s|^---$", between, re.MULTILINE):
            continue
        redundant_labels.append(f"{current.group(1)}@L{draft.count(chr(10), 0, current.start()) + 1}")
    if redundant_labels:
        findings.append(
            Finding(
                "ERROR",
                "REDUNDANT_SPEAKER_LABEL",
                "同一说话人连续发言被重复署名，应保留自然分段但合并姓名：" + "、".join(redundant_labels[:10]),
                str(path),
            )
        )
    repeated = duplicate_paragraphs(draft)
    if repeated:
        findings.append(
            Finding(
                "ERROR",
                "DUPLICATE_PARAGRAPH",
                f"发布稿有 {len(repeated)} 个长段落完全重复："
                + "、".join(paragraph[:60] for paragraph in repeated[:5]),
                str(path),
            )
        )
    near_repeated = near_duplicate_paragraphs(draft)
    if near_repeated:
        findings.append(
            Finding(
                "WARNING",
                "NEAR_DUPLICATE_PARAGRAPH",
                f"发现 {len(near_repeated)} 组同一说话人的近义重复，需判断是有效复述还是同一来源被重复扩写："
                + "；".join(near_repeated[:5]),
                str(path),
            )
        )

    paragraph_lengths = [
        (speaker, len(text), text[:60])
        for speaker, text in draft_speaker_paragraphs(draft)
    ]
    oversized_paragraphs = [
        item for item in paragraph_lengths if item[1] > MAX_DIALOGUE_PARAGRAPH_CHARS
    ]
    long_paragraphs = [
        item
        for item in paragraph_lengths
        if LONG_DIALOGUE_PARAGRAPH_CHARS < item[1] <= MAX_DIALOGUE_PARAGRAPH_CHARS
    ]
    if oversized_paragraphs:
        findings.append(
            Finding(
                "ERROR",
                "OVERSIZED_DIALOGUE_PARAGRAPH",
                "发现超过500字、会破坏长文节奏的对话段落："
                + "；".join(
                    f"{speaker}{length}字：{snippet}"
                    for speaker, length, snippet in oversized_paragraphs[:5]
                ),
                str(path),
            )
        )
    if long_paragraphs:
        findings.append(
            Finding(
                "WARNING",
                "LONG_DIALOGUE_PARAGRAPH",
                "发现超过300字的对话段落，请判断能否在不改意的前提下自然分段："
                + "；".join(
                    f"{speaker}{length}字：{snippet}"
                    for speaker, length, snippet in long_paragraphs[:5]
                ),
                str(path),
            )
        )

    section_matches = list(re.finditer(r"^##\s+(.+)$", draft, re.MULTILINE))
    if len(section_matches) >= 3 and len(normalize_text(draft)) >= MIN_LONG_INTERVIEW_CHARS:
        section_sizes: list[tuple[str, int]] = []
        for index, match in enumerate(section_matches):
            end = section_matches[index + 1].start() if index + 1 < len(section_matches) else len(draft)
            section_sizes.append((match.group(1), len(normalize_text(draft[match.end() : end]))))
        total_section_chars = sum(size for _, size in section_sizes)
        if total_section_chars:
            imbalanced = [
                (heading, size / total_section_chars)
                for heading, size in section_sizes
                if size / total_section_chars < 0.10 or size / total_section_chars > 0.35
            ]
            if imbalanced:
                findings.append(
                    Finding(
                        "WARNING",
                        "SECTION_SIZE_IMBALANCE",
                        "章节体量过碎或过宽，需复查导航是否按自然板块划分："
                        + "、".join(f"{heading[:28]}={share:.1%}" for heading, share in imbalanced[:6]),
                        str(path),
                    )
                )

    draft_without_labels = NAMED_SPEAKER_RE.sub("", draft)
    content_emphasis = [
        item.strip()
        for item in re.findall(r"\*\*([^*\n]{4,180})\*\*", draft_without_labels)
        if item.strip() not in NON_SPEAKER_LABELS
    ]
    if (
        len(normalize_text(draft)) >= MIN_LONG_INTERVIEW_CHARS
        and h2
        and len(content_emphasis) < len(h2)
    ):
        findings.append(
            Finding(
                "WARNING",
                "SPARSE_READING_LANDMARKS",
                f"长文只有{len(content_emphasis)}处非署名重点加粗，少于{len(h2)}个章节；请从已核原句中设置可跳读的视觉锚点，不要另造金句。",
                str(path),
            )
        )

    noisy_turns: list[str] = []
    asr_residue_turns: list[str] = []
    repeated_fillers = re.compile(
        r"(?:我我|(?<!现)在在|但但是|因因为|然后然后|就是就是|觉得觉得|然后当而且)"
    )
    for match in DRAFT_TURN_RE.finditer(draft):
        speaker = match.group(1)
        if speaker in NON_SPEAKER_LABELS:
            continue
        content = non_whitespace_text(match.group(2))
        if repeated_fillers.search(content):
            asr_residue_turns.append(f"{speaker}：{content[:70]}")
        if len(content) < 80:
            continue
        filler_count = content.count("然后") + content.count("就是")
        filler_density = filler_count / len(content) * 100
        if filler_count >= 5 and filler_density >= 2:
            noisy_turns.append(f"{speaker}：{content[:70]}")
    if asr_residue_turns:
        findings.append(
            Finding(
                "ERROR",
                "ASR_RESIDUE",
                f"发现 {len(asr_residue_turns)} 个明显口吃或机器拼接残留："
                + "；".join(asr_residue_turns[:5]),
                str(path),
            )
        )
    if noisy_turns:
        findings.append(
            Finding(
                "WARNING",
                "ORAL_FILLER_OVERLOAD",
                f"发现 {len(noisy_turns)} 个疑似未完成口头禅降噪的长发言："
                + "；".join(noisy_turns[:5]),
                str(path),
            )
        )

    dialogue_turns = [
        match.group(2)
        for match in DRAFT_TURN_RE.finditer(draft)
        if match.group(1) not in NON_SPEAKER_LABELS
    ]
    ending_sample = "\n".join(dialogue_turns[-12:])
    shutdown_markers = [
        label
        for label, pattern in SHUTDOWN_TAIL_PATTERNS.items()
        if pattern.search(ending_sample)
    ]
    if len(shutdown_markers) >= 2:
        findings.append(
            Finding(
                "ERROR",
                "POST_CONTENT_SHUTDOWN_CHATTER",
                "发布稿结尾仍保留多类关播仪式性闲聊（"
                + "、".join(shutdown_markers)
                + "）。应在最后一个有实质内容的回答处收束，追溯需要由完整底稿承担。",
                str(path),
            )
        )
    ending_runway_sample = "\n".join(dialogue_turns[-32:])
    ending_runway_markers = [
        label
        for label, pattern in ENDING_RUNWAY_PATTERNS.items()
        if pattern.search(ending_runway_sample)
    ]
    if len(ending_runway_markers) >= 2:
        findings.append(
            Finding(
                "WARNING",
                "ENDING_RUNWAY_DRIFT",
                "最终总结问答之前仍有多类低价值转场（"
                + "、".join(ending_runway_markers)
                + "）。请独立复查最后10–15分钟，只保留推进人物未来、主题回响或关系变化的内容。",
                str(path),
            )
        )
    if TIMESTAMP_RE.search(draft):
        findings.append(Finding("WARNING", "TIMECODE_IN_DRAFT", "发布稿中检测到时间码，请确认是否需要展示。", str(path)))

    unfinished = sorted(set(match.group() for match in UNFINISHED_RE.finditer(draft)))
    if unfinished:
        findings.append(
            Finding(
                "ERROR",
                "UNFINISHED_PLACEHOLDER",
                f"发布稿存在未完成占位：{', '.join(unfinished[:10])}",
                str(path),
            )
        )

    support_values = supported_number_values(support)
    checked_draft = remove_section_ordinals(draft)
    unsupported_numbers = sorted(
        {
            match.group()
            for match in ARABIC_NUMBER_RE.finditer(checked_draft)
            if float(match.group()) not in support_values
        },
        key=lambda value: float(value),
    )
    if unsupported_numbers:
        findings.append(
            Finding(
                "ERROR",
                "NEW_NUMBER",
                "发布稿中的数字未在原稿/背景资料找到：" + ", ".join(unsupported_numbers),
                str(path),
            )
        )

    unsupported_terms = sorted(term for term in candidate_terms(draft) if term not in support)
    if unsupported_terms:
        findings.append(
            Finding(
                "WARNING",
                "NEW_TERM",
                "以下疑似专名/引用未在原稿或背景资料找到："
                + "、".join(unsupported_terms[:20]),
                str(path),
            )
        )

    for label, pattern in RISK_PATTERNS.items():
        matches = sorted(set(match.group() for match in pattern.finditer(draft)))
        if matches:
            findings.append(
                Finding(
                    "WARNING",
                    "HIGH_RISK_CLAIM",
                    f"检测到{label}，需回听并确认已进入待核清单：{'、'.join(matches[:10])}",
                    str(path),
                )
            )

    grounding_ratios, grounding = paragraph_grounding_profile(draft, grounding_source or support)
    if len(grounding_ratios) >= MIN_GROUNDING_PARAGRAPHS:
        ordered = sorted(grounding_ratios)
        median = ordered[len(ordered) // 2]
        p10 = ordered[len(ordered) // 10]
        low_share = sum(value < LOW_GROUNDING_THRESHOLD for value in ordered) / len(ordered)
        if median < MIN_GROUNDING_MEDIAN or low_share > MAX_LOW_GROUNDING_SHARE:
            findings.append(
                Finding(
                    "ERROR",
                    "HEAVY_REWRITE_DENSITY",
                    f"正文段落来源三字片段重合度中位数为 {median:.1%}，低来源段落占 {low_share:.1%}；"
                    "整体已偏离轻度校订，需回到原话重做，不得仅凭主题相同放行。",
                    str(path),
                )
            )
        elif median < TARGET_GROUNDING_MEDIAN or p10 < TARGET_GROUNDING_P10:
            findings.append(
                Finding(
                    "WARNING",
                    "GROUNDING_REVIEW",
                    f"正文段落来源三字片段重合度中位数为 {median:.1%}、P10为 {p10:.1%}，低来源段落占 {low_share:.1%}；请复核是否把同一追问链压成编辑摘要。",
                    str(path),
                )
            )
    if grounding:
        findings.append(
            Finding(
                "WARNING",
                "LOW_GROUNDING",
                f"有 {len(grounding)} 个长段落低于 {LOW_GROUNDING_THRESHOLD:.0%} 来源重合度，请人工检查。\n- "
                + "\n- ".join(grounding[:20]),
                str(path),
            )
        )
    return findings


def audit_title_number_inventory(draft: str, issues: str, path: Path) -> list[Finding]:
    """Every Arabic numeric claim in the public H1 must have a verification row."""
    title_match = re.search(r"^#\s+(.+)$", draft, re.MULTILINE)
    if not title_match:
        return []
    title_values = supported_number_values(title_match.group(1))
    if not title_values:
        return []
    issue_rows = "\n".join(
        line
        for line in issues.splitlines()
        if re.match(r"^\|\s*P[01]\s*\|", line)
    )
    issue_values = supported_number_values(issue_rows)
    missing = sorted(value for value in title_values if value not in issue_values)
    if not missing:
        return []
    labels = [str(int(value)) if value.is_integer() else str(value) for value in missing]
    return [
        Finding(
            "ERROR",
            "TITLE_NUMBER_NOT_IN_ISSUES",
            "标题中的数字必须逐项进入P0/P1核验清单，即使原转写已经出现也不能只靠ASR放行："
            + "、".join(labels),
            str(path),
        )
    ]


def audit_issue_list(text: str, path: Path, *, source: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    source_timestamps = (
        [timestamp for _, timestamp in SOURCE_SPEAKER_RE.findall(source)] if source else []
    )
    source_end = max((time_to_seconds(value) for value in source_timestamps), default=None)
    required_columns = ({"级别", "内部标记"}, {"时间码", "回放位置"}, {"问题类型", "哪类问题"},
                        {"原文/成稿", "需要核对的原话或文字"}, {"处理建议", "建议怎么处理"}, {"状态", "目前情况"})
    headers = [[cell.strip() for cell in line.strip().strip('|').split('|')]
               for line in text.splitlines() if line.lstrip().startswith('|')]
    if not any(len(cells) == 6 and all(cell in allowed for cell, allowed in zip(cells, required_columns)) for cells in headers):
        findings.append(Finding("ERROR", "ISSUE_COLUMNS", "待核清单缺少固定表格列。", str(path)))

    table_counts = {"P0": 0, "P1": 0, "P2": 0}
    pending_counts = {"P0": 0, "P1": 0, "P2": 0}
    for line in text.splitlines():
        row = re.match(r"^\|\s*(P[012])\s*\|", line)
        if not row:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        priority = row.group(1)
        status = cells[-1] if cells else ""
        time_cell = cells[1] if len(cells) > 1 else ""
        table_counts[priority] += 1
        if status == "待确认":
            pending_counts[priority] += 1
        if status not in ALLOWED_ISSUE_STATUSES | {"已保守表达"}:
            findings.append(
                Finding("ERROR", "ISSUE_STATUS", f"待核清单存在无效状态“{status}”：{line[:160]}", str(path))
            )
        if priority in {"P0", "P1"} and status == "待确认":
            timestamps = TIMESTAMP_RE.findall(time_cell)
            if not timestamps:
                findings.append(
                    Finding(
                        "ERROR",
                        "ISSUE_NO_PRECISE_TIMECODE",
                        f"未关闭 {priority} 必须给出事实出现的精确时间码：{line[:160]}",
                        str(path),
                    )
                )
            elif source_end is not None:
                out_of_range = [value for value in timestamps if time_to_seconds(value) > source_end]
                if out_of_range:
                    findings.append(
                        Finding(
                            "ERROR",
                            "ISSUE_TIMECODE_OUT_OF_RANGE",
                            f"待核时间码超出原稿最后发言时间：{'、'.join(out_of_range)}。",
                            str(path),
                        )
                    )
            for segment in re.split(r"[、,，]", time_cell):
                segment_times = TIMESTAMP_RE.findall(segment)
                if len(segment_times) >= 2:
                    duration = time_to_seconds(segment_times[1]) - time_to_seconds(segment_times[0])
                    if duration < 0:
                        findings.append(
                            Finding(
                                "ERROR",
                                "ISSUE_TIMECODE_ORDER",
                                f"待核范围起止时间倒置：{segment.strip()}",
                                str(path),
                            )
                        )
                    elif duration > 90:
                        findings.append(
                            Finding(
                                "ERROR",
                                "ISSUE_TIMECODE_TOO_BROAD",
                                f"待核范围超过90秒，应定位到事实所在发言而不是整段主题：{segment.strip()}",
                                str(path),
                            )
                        )

    p0_summary = re.search(r"^- P0：\s*(\d+)", text, re.MULTILINE)
    if not p0_summary:
        findings.append(Finding("WARNING", "P0_SUMMARY", "待核清单未填写 P0 汇总。", str(path)))
    elif int(p0_summary.group(1)):
        findings.append(Finding("ERROR", "OPEN_P0", f"待核清单仍有 {p0_summary.group(1)} 个 P0。", str(path)))

    for priority in ("P0", "P1", "P2"):
        summary = re.search(rf"^- {priority}：\s*(\d+)[^\n]*", text, re.MULTILINE)
        if not summary:
            findings.append(Finding("ERROR", "ISSUE_SUMMARY", f"待核清单缺少 {priority} 汇总。", str(path)))
            continue
        stated = int(summary.group(1))
        if "待确认" in summary.group(0):
            expected = pending_counts[priority]
        else:
            expected = table_counts[priority]
        if stated != expected:
            findings.append(
                Finding(
                    "ERROR",
                    "ISSUE_SUMMARY_MISMATCH",
                    f"{priority} 汇总写 {stated}，表格实际应为 {expected}。",
                    str(path),
                )
            )

    for line in text.splitlines():
        priority = re.match(r"^\|\s*(P[01])\s*\|", line)
        if not priority:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        status = cells[-1] if cells else ""
        if priority.group(1) == "P0" and status not in {"已回听", "已当事人确认", "已删除", "已保守表达", "已解决"}:
            findings.append(Finding("ERROR", "UNRESOLVED_P0", f"存在未关闭 P0：{line[:160]}", str(path)))
        elif priority.group(1) == "P1" and status == "待确认":
            findings.append(Finding("WARNING", "UNRESOLVED_P1", f"存在未关闭 P1：{line[:160]}", str(path)))
    return findings


def conflicting_review_filenames(
    clip_items: list[object], sample_items: list[object]
) -> set[str]:
    """Allow issue references to share one exact window, but nothing else."""
    windows: dict[str, tuple[object, object]] = {}
    conflicts: set[str] = set()
    for item in clip_items:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
            continue
        filename = item["filename"]
        window = (item.get("start"), item.get("duration"))
        if filename in windows and windows[filename] != window:
            conflicts.add(filename)
        else:
            windows[filename] = window
    samples_seen: set[str] = set()
    for item in sample_items:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
            continue
        filename = item["filename"]
        if filename in windows or filename in samples_seen:
            conflicts.add(filename)
        samples_seen.add(filename)
    return conflicts


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_machine_transcripts(
    clip_dir: Path,
    manifest_path: Path,
    expected_filenames: set[str],
) -> list[Finding]:
    transcript_path = clip_dir / "machine_transcripts.json"
    if not transcript_path.is_file():
        return []
    try:
        data = json.loads(read_text(transcript_path))
    except json.JSONDecodeError as exc:
        return [
            Finding(
                "ERROR",
                "MACHINE_TRANSCRIPT_JSON",
                f"机器辅助转写JSON无效：{exc}",
                str(transcript_path),
            )
        ]
    findings: list[Finding] = []
    if data.get("manifest_sha256") != file_sha256(manifest_path):
        findings.append(
            Finding(
                "ERROR",
                "MACHINE_TRANSCRIPT_MANIFEST_DRIFT",
                "机器辅助转写对应的回听manifest已变化，必须重新生成，不能沿用旧结果。",
                str(transcript_path),
            )
        )
    clips = data.get("clips")
    if not isinstance(clips, list):
        return findings + [
            Finding(
                "ERROR",
                "MACHINE_TRANSCRIPT_STRUCTURE",
                "机器辅助转写必须包含clips数组。",
                str(transcript_path),
            )
        ]
    entries = {
        item.get("filename"): item
        for item in clips
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    }
    if set(entries) != expected_filenames:
        findings.append(
            Finding(
                "ERROR",
                "MACHINE_TRANSCRIPT_SET_DRIFT",
                "机器辅助转写覆盖的音频集合与当前manifest不一致。",
                str(transcript_path),
            )
        )
    bad_hashes: list[str] = []
    failed: list[str] = []
    for filename, item in entries.items():
        audio_path = clip_dir / filename
        if audio_path.is_file() and item.get("audio_sha256") != file_sha256(audio_path):
            bad_hashes.append(filename)
        if item.get("error"):
            failed.append(filename)
    if bad_hashes:
        findings.append(
            Finding(
                "ERROR",
                "MACHINE_TRANSCRIPT_AUDIO_DRIFT",
                f"机器辅助转写与当前音频哈希不一致：{bad_hashes[:3]}",
                str(transcript_path),
            )
        )
    if failed:
        findings.append(
            Finding(
                "ERROR",
                "MACHINE_TRANSCRIPT_INCOMPLETE",
                f"机器辅助转写有 {len(failed)} 段失败：{failed[:3]}",
                str(transcript_path),
            )
        )
    return findings


def audit_review_index(
    index_path: Path,
    manifest_path: Path,
    expected_filenames: set[str],
) -> list[Finding]:
    if not index_path.is_file():
        return [
            Finding(
                "ERROR",
                "MISSING_REVIEW_INDEX",
                "缺少可点击播放的 _work/回听辅助索引.md；终审者不应手工在文件夹中逐个寻找音频。",
                str(index_path),
            )
        ]
    text = read_text(index_path)
    manifest_match = re.search(r"<!-- manifest_sha256: ([0-9a-f]{64}) -->", text)
    machine_match = re.search(
        r"<!-- machine_transcripts_sha256: (none|[0-9a-f]{64}) -->", text
    )
    transcript_path = manifest_path.parent / "machine_transcripts.json"
    expected_machine = file_sha256(transcript_path) if transcript_path.is_file() else "none"
    findings: list[Finding] = []
    if not manifest_match or manifest_match.group(1) != file_sha256(manifest_path):
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_INDEX_MANIFEST_DRIFT",
                "回听辅助索引与当前音频manifest不一致，必须重新生成。",
                str(index_path),
            )
        )
    if not machine_match or machine_match.group(1) != expected_machine:
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_INDEX_TRANSCRIPT_DRIFT",
                "回听辅助索引没有反映当前机器辅助转写，必须重新生成。",
                str(index_path),
            )
        )
    missing_links = sorted(
        filename
        for filename in expected_filenames
        if f"review_clips/{filename}" not in text
    )
    if missing_links:
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_INDEX_FILE_GAP",
                f"回听辅助索引缺少 {len(missing_links)} 个音频入口：{missing_links[:3]}",
                str(index_path),
            )
        )
    return findings


def audit_review_sheet(
    issues: str,
    source: str,
    path: Path,
    *,
    replay_exists: bool,
) -> list[Finding]:
    pending_p1 = sum(
        1
        for line in issues.splitlines()
        if re.match(r"^\|\s*P1\s*\|", line)
        and [cell.strip() for cell in line.strip().strip("|").split("|")][-1] == "待确认"
    )
    long_interview = len(source_interview_content(source)) >= MIN_LONG_INTERVIEW_CHARS
    if pending_p1 < 5 and not (long_interview and replay_exists):
        return []
    if not path.is_file():
        reason = (
            f"存在 {pending_p1} 项待确认P1"
            if pending_p1
            else "长访谈有回放，仍需完成全场随机抽听"
        )
        return [
            Finding(
                "ERROR",
                "MISSING_REVIEW_SHEET",
                f"{reason}；必须生成 _work/{REVIEW_SHEET_NAME} 供集中回听。",
                str(path),
            )
        ]
    findings: list[Finding] = []
    sheet = read_text(path)
    rows = re.findall(r"^\|\s*\d+\s*\|", sheet, re.MULTILINE)
    if len(rows) < pending_p1:
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_SHEET_DRIFT",
                f"回听终审表只有 {len(rows)} 个问题行，但待核清单仍有 {pending_p1} 项待确认P1；请刷新终审表。",
                str(path),
            )
        )
    sample_rows = re.findall(r"`SAMPLE-\d{2}-[^`]+\.m4a`", sheet)
    if len(sample_rows) < 5:
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_SHEET_MISSING_RANDOM_SAMPLES",
                "回听终审表必须包含至少5段避开P1的分层随机抽听音频。",
                str(path),
            )
        )

    clip_dir = path.parent / "review_clips"
    manifest_path = clip_dir / "manifest.json"
    if not manifest_path.is_file():
        findings.append(
            Finding(
                "ERROR",
                "MISSING_REVIEW_CLIP_MANIFEST",
                "回听终审表缺少对应的 _work/review_clips/manifest.json。",
                str(manifest_path),
            )
        )
        return findings
    try:
        manifest = json.loads(read_text(manifest_path))
    except json.JSONDecodeError as exc:
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_CLIP_MANIFEST_JSON",
                f"回听音频清单 JSON 无效：{exc}",
                str(manifest_path),
            )
        )
        return findings

    clip_items = manifest.get("clips", [])
    sample_items = manifest.get("samples", [])
    if not isinstance(clip_items, list) or not isinstance(sample_items, list):
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_CLIP_MANIFEST_STRUCTURE",
                "回听音频清单必须包含 clips 和 samples 两个数组。",
                str(manifest_path),
            )
        )
        return findings
    items = [item for item in clip_items + sample_items if isinstance(item, dict)]
    filenames = [item.get("filename") for item in items if isinstance(item.get("filename"), str)]
    conflicts = conflicting_review_filenames(clip_items, sample_items)
    if conflicts:
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_CLIP_DUPLICATE_FILENAME",
                f"回听音频同名文件指向不同窗口，或随机抽听发生重复：{sorted(conflicts)[:3]}",
                str(manifest_path),
            )
        )
    issue_indexes = {
        item.get("issue_index")
        for item in clip_items
        if isinstance(item, dict) and isinstance(item.get("issue_index"), int)
    }
    if len(issue_indexes) < pending_p1:
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_CLIP_ISSUE_GAP",
                f"回听音频只覆盖 {len(issue_indexes)} 个问题，但仍有 {pending_p1} 项待确认P1。",
                str(manifest_path),
            )
        )
    sample_filenames = {
        item.get("filename")
        for item in sample_items
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    }
    if len(sample_filenames) < 5:
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_CLIP_SAMPLE_GAP",
                "回听音频清单必须包含至少5段分层随机抽听。",
                str(manifest_path),
            )
        )
    missing_files = sorted(filename for filename in filenames if not (clip_dir / filename).is_file())
    if missing_files:
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_CLIP_FILE_MISSING",
                f"回听音频清单中有 {len(missing_files)} 个文件不存在：{missing_files[:3]}",
                str(manifest_path),
            )
        )
    sheet_filenames = set(re.findall(r"`([^`]+\.m4a)`", sheet))
    manifest_filenames = set(filenames)
    if sheet_filenames != manifest_filenames:
        findings.append(
            Finding(
                "ERROR",
                "REVIEW_CLIP_SHEET_DRIFT",
                "回听终审表引用的音频与 manifest 不一致；不得在保留旧终审表时静默替换抽听材料。",
                str(path),
            )
        )
    findings.extend(
        audit_machine_transcripts(clip_dir, manifest_path, manifest_filenames)
    )
    findings.extend(
        audit_review_index(
            path.parent / REVIEW_INDEX_NAME,
            manifest_path,
            manifest_filenames,
        )
    )
    return findings


def audit_report(
    text: str,
    path: Path,
    *,
    ratio: float | None = None,
    draft: str | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    efficiency_labels = [
        "AI 整理耗时",
        "人工终审耗时",
        "总耗时",
        "直接费用",
        "相比原工作流程节省时间",
    ]
    missing_efficiency = []
    for label in efficiency_labels:
        label_pattern = r"AI\s*整理耗时" if label == "AI 整理耗时" else re.escape(label)
        if not re.search(rf"^- {label_pattern}：\s*\S.+$", text, re.MULTILINE):
            missing_efficiency.append(label)
    if missing_efficiency:
        findings.append(
            Finding(
                "ERROR",
                "MISSING_EFFICIENCY_RECORD",
                "质检报告缺少效率记录：" + "、".join(missing_efficiency),
                str(path),
            )
        )
    readthrough = re.search(r"^- 发布稿独立通读：\s*(\S.+)$", text, re.MULTILINE)
    if not readthrough:
        findings.append(
            Finding(
                "ERROR",
                "MISSING_PUBLISH_DRAFT_READTHROUGH",
                "质检报告必须记录发布稿独立通读状态；完整底稿完成不等于发布稿已经按真人阅读体验复审。",
                str(path),
            )
        )
    if draft is not None:
        normalized_draft = normalize_text(draft)
        stale_phrases = [
            phrase
            for phrase in re.findall(r"暂按“([^”]{4,80})”", text)
            if normalize_text(phrase) not in normalized_draft
        ]
        if stale_phrases:
            findings.append(
                Finding(
                    "ERROR",
                    "REPORT_DRAFT_SEMANTIC_DRIFT",
                    "质检报告声称正文暂按某种写法处理，但当前发布稿已不存在该写法："
                    + "、".join(stale_phrases[:5]),
                    str(path),
                )
            )
    test_type_match = re.search(r"^- 测试类型：\s*(.+)$", text, re.MULTILINE)
    allowed_test_types = {"标杆学习", "已知标杆回归", "无人工成稿独立整理", "双轨对比"}
    if not test_type_match or test_type_match.group(1).strip() not in allowed_test_types:
        findings.append(
            Finding(
                "ERROR",
                "TEST_TYPE",
                "质检报告测试类型必须是：标杆学习、已知标杆回归、无人工成稿独立整理或双轨对比。",
                str(path),
            )
        )
    score_kind_match = re.search(r"^- 评分性质：\s*(.+)$", text, re.MULTILINE)
    score_kind = score_kind_match.group(1).strip() if score_kind_match else ""
    if score_kind not in {"AI预评", "人工终审", "独立双轨评审"}:
        findings.append(
            Finding(
                "ERROR",
                "SCORE_KIND",
                "质检报告必须标明评分性质：AI预评、人工终审或独立双轨评审。",
                str(path),
            )
        )
    dimensions = {
        "忠实与可追溯": 35,
        "原始内容完整度": 30,
        "对谈现场与人物原声": 20,
        "阅读导航与节奏": 10,
        "排版与运营完成度": 5,
    }
    scores: list[int] = []
    score_by_dimension: dict[str, int] = {}
    for dimension, maximum in dimensions.items():
        match = re.search(rf"^\|\s*{re.escape(dimension)}\s*\|\s*{maximum}\s*\|\s*(\d+)\s*\|", text, re.MULTILINE)
        if not match:
            findings.append(Finding("ERROR", "MISSING_SCORE", f"质检报告未填写「{dimension}」得分。", str(path)))
            continue
        score = int(match.group(1))
        scores.append(score)
        score_by_dimension[dimension] = score
        if score > maximum:
            findings.append(Finding("ERROR", "SCORE_RANGE", f"「{dimension}」得分 {score} 超过权重 {maximum}。", str(path)))
    if len(scores) == len(dimensions) and sum(scores) < 90:
        findings.append(Finding("WARNING", "LOW_SCORE", f"质检总分为 {sum(scores)}，低于原声沉浸版建议线 90。", str(path)))
    if score_kind == "AI预评":
        findings.append(
            Finding(
                "WARNING",
                "AI_SELF_SCORE",
                "当前分数是AI预评，只用于定位短板，不能作为已经达到或超过人工团队的证据。",
                str(path),
            )
        )
    comparison = re.search(r"^- 对比结论：\s*(.+)$", text, re.MULTILINE)
    if comparison and "AI优于人工" in comparison.group(1):
        if score_kind != "独立双轨评审" or not test_type_match or test_type_match.group(1).strip() != "双轨对比":
            findings.append(
                Finding(
                    "ERROR",
                    "UNSUPPORTED_HUMAN_SUPERIORITY_CLAIM",
                    "只有双轨对比且评分性质为独立双轨评审时，才能写“AI优于人工”。",
                    str(path),
                )
            )
        input_parity = re.search(r"^- 输入一致性：\s*(.+)$", text, re.MULTILINE)
        source_reviewer = re.search(r"^- 来源评审人：\s*(.+)$", text, re.MULTILINE)
        reader_reviewer = re.search(r"^- 读者评审人：\s*(.+)$", text, re.MULTILINE)
        reader_count = re.search(r"^- 有效读者人数：\s*(\d+)\s*$", text, re.MULTILINE)
        evidence_ready = (
            input_parity
            and input_parity.group(1).strip() == "是"
            and source_reviewer
            and source_reviewer.group(1).strip() not in {"", "待填"}
            and reader_reviewer
            and reader_reviewer.group(1).strip() not in {"", "待填"}
            and reader_count
            and int(reader_count.group(1)) >= 3
        )
        if not evidence_ready:
            findings.append(
                Finding(
                    "ERROR",
                    "MISSING_DUAL_TRACK_EVIDENCE",
                    "声称AI优于人工时，必须证明输入一致，填写来源评审人、读者评审人，并至少有3名匿名读者评审。",
                    str(path),
                )
            )

    completeness = score_by_dimension.get("原始内容完整度")
    if (
        ratio is not None
        and ratio < MIN_IMMERSIVE_RETENTION_RATIO
        and completeness is not None
        and completeness > 24
    ):
        findings.append(
            Finding(
                "ERROR",
                "SCORE_CAP_OVER_COMPRESSION",
                f"纯问答字符保留率 {ratio:.2%} 低于原声沉浸版硬下限，原始内容完整度不得超过 24/30，当前为 {completeness}。",
                str(path),
            )
        )

    fidelity = score_by_dimension.get("忠实与可追溯")
    if fidelity is not None and fidelity > 31 and re.search(r"(?:尚未|未)完成.{0,12}音频级", text):
        findings.append(
            Finding(
                "ERROR",
                "SCORE_CAP_NO_AUDIO_REVIEW",
                f"报告说明尚未完成音频级核验，忠实与可追溯不得超过 31/35，当前为 {fidelity}。",
                str(path),
            )
        )

    unchecked = re.findall(r"^- \[ \] .+", text, re.MULTILINE)
    if unchecked:
        findings.append(Finding("ERROR", "UNCHECKED_RELEASE_ITEM", f"发布阻断检查仍有 {len(unchecked)} 项未勾选。", str(path)))

    publishable = re.search(r"^- 是否可发布：\s*(.+)$", text, re.MULTILINE)
    publish_status = publishable.group(1).strip() if publishable else ""
    if publish_status not in {"是", "可发布", "有条件可发布", "待人工终审"}:
        findings.append(Finding("ERROR", "INVALID_RELEASE_STATUS", "质检报告必须明确标记为可发布、有条件可发布或待人工终审。", str(path)))
    elif publish_status == "有条件可发布":
        release_condition = re.search(r"^- 发布条件：\s*(.+)$", text, re.MULTILINE)
        if not release_condition or not release_condition.group(1).strip():
            findings.append(Finding("ERROR", "MISSING_RELEASE_CONDITION", "有条件可发布时必须填写发布条件。", str(path)))
        else:
            findings.append(Finding("WARNING", "CONDITIONAL_RELEASE", "稿件仍需完成质检报告中的发布条件。", str(path)))
    elif publish_status == "待人工终审":
        release_condition = re.search(r"^- 发布条件：\s*(.+)$", text, re.MULTILINE)
        if not release_condition or not release_condition.group(1).strip():
            findings.append(Finding("ERROR", "MISSING_RELEASE_CONDITION", "待人工终审时必须填写尚未完成的发布条件。", str(path)))
        else:
            findings.append(Finding("WARNING", "PENDING_FINAL_REVIEW", "机器审计通过，但稿件仍处于待人工终审状态。", str(path)))
    return findings


def audit_evidence(
    text: str,
    source: str,
    draft: str,
    path: Path,
    *,
    edit_ledger: str | None = None,
    coverage: str | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [Finding("ERROR", "EVIDENCE_JSON", f"证据映射 JSON 无效：{exc}", str(path))]

    if data.get("version") != 1:
        findings.append(Finding("ERROR", "EVIDENCE_VERSION", "证据映射 version 必须为 1。", str(path)))
    if data.get("source") != f"inputs/{SOURCE_NAME}":
        findings.append(
            Finding(
                "ERROR",
                "EVIDENCE_SOURCE",
                f"证据映射 source 必须为 inputs/{SOURCE_NAME}。",
                str(path),
            )
        )
    sections = data.get("sections")
    if not isinstance(sections, list):
        return findings + [Finding("ERROR", "EVIDENCE_SECTIONS", "证据映射 sections 必须为数组。", str(path))]

    draft_section_matches = list(re.finditer(r"^##\s+(.+)$", draft, re.MULTILINE))
    draft_sections = [match.group(1) for match in draft_section_matches]
    draft_section_bodies: dict[str, str] = {}
    for index, match in enumerate(draft_section_matches):
        end = draft_section_matches[index + 1].start() if index + 1 < len(draft_section_matches) else len(draft)
        draft_section_bodies[normalize_text(match.group(1))] = draft[match.end() : end]
    long_interview = len(source_interview_content(source)) >= MIN_LONG_INTERVIEW_CHARS
    if len(sections) < len(draft_sections):
        findings.append(
            Finding(
                "ERROR",
                "EVIDENCE_COVERAGE",
                f"发布稿有 {len(draft_sections)} 个 H2，证据映射仅有 {len(sections)} 个章节。",
                str(path),
            )
        )

    if long_interview and len(sections) < 8:
        findings.append(
            Finding(
                "ERROR",
                "EVIDENCE_TOO_COARSE",
                "长访谈证据映射少于8个语义模块；只按大章节映射无法支持逐段终审。",
                str(path),
            )
        )

    ledger_ids: set[str] = set()
    if edit_ledger:
        try:
            ledger_data = json.loads(edit_ledger)
            ledger_ids = {
                str(item.get("id", "")).strip()
                for item in ledger_data.get("nontrivial_edits", [])
                if isinstance(item, dict) and str(item.get("id", "")).strip()
            }
        except json.JSONDecodeError:
            pass

    source_timestamps = set(TIMESTAMP_RE.findall(source))
    seen_module_ids: set[str] = set()
    mapped_ledger_ids: set[str] = set()
    evidence_intervals: list[tuple[int, int]] = []
    evidence_headings: set[str] = set()
    previous_module_start: int | None = None
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict) or not section.get("draft_heading"):
            findings.append(Finding("ERROR", "EVIDENCE_HEADING", f"第 {index} 个证据章节缺少 draft_heading。", str(path)))
            continue
        if long_interview:
            evidence_headings.add(normalize_text(str(section.get("draft_heading", ""))))
            module_id = str(section.get("module_id", "")).strip()
            if not module_id or module_id in seen_module_ids:
                findings.append(
                    Finding(
                        "ERROR",
                        "EVIDENCE_MODULE_ID",
                        f"第 {index} 个语义模块缺少唯一 module_id。",
                        str(path),
                    )
                )
            seen_module_ids.add(module_id)

            anchor = str(section.get("draft_anchor", "")).strip()
            normalized_anchor = normalize_text(anchor)
            anchor_count = normalize_text(draft).count(normalized_anchor) if normalized_anchor else 0
            if len(normalized_anchor) < 8 or anchor_count != 1:
                findings.append(
                    Finding(
                        "ERROR",
                        "EVIDENCE_DRAFT_ANCHOR",
                        f"{module_id or index} 的 draft_anchor 必须能在成稿中唯一定位，当前匹配 {anchor_count} 次。",
                        str(path),
                    )
                )
            heading_key = normalize_text(str(section.get("draft_heading", "")))
            heading_body = draft_section_bodies.get(heading_key)
            if (
                heading_body is not None
                and len(normalized_anchor) >= 8
                and normalized_anchor not in normalize_text(heading_body)
            ):
                findings.append(
                    Finding(
                        "ERROR",
                        "EVIDENCE_ANCHOR_HEADING_DRIFT",
                        f"{module_id or index} 的锚点不在其声明章节“{section.get('draft_heading')}”内。",
                        str(path),
                    )
                )
            key_details = section.get("key_details")
            if not isinstance(key_details, list) or not any(str(item).strip() for item in key_details):
                findings.append(
                    Finding(
                        "ERROR",
                        "EVIDENCE_KEY_DETAILS",
                        f"{module_id or index} 缺少关键细节清单。",
                        str(path),
                    )
                )
            status = section.get("verification_status")
            if status not in ALLOWED_EVIDENCE_STATUSES:
                findings.append(
                    Finding(
                        "ERROR",
                        "EVIDENCE_STATUS",
                        f"{module_id or index} 的核验状态无效：{status}",
                        str(path),
                    )
                )
            decision_ids = section.get("edit_decision_ids")
            if not isinstance(decision_ids, list):
                findings.append(
                    Finding(
                        "ERROR",
                        "EVIDENCE_EDIT_IDS",
                        f"{module_id or index} 的 edit_decision_ids 必须为数组。",
                        str(path),
                    )
                )
            else:
                unknown_ids = {str(item).strip() for item in decision_ids} - ledger_ids
                if unknown_ids:
                    findings.append(
                        Finding(
                            "ERROR",
                            "EVIDENCE_UNKNOWN_EDIT_ID",
                            f"{module_id or index} 引用了不存在的编辑决策：{sorted(unknown_ids)}",
                            str(path),
                        )
                    )
                mapped_ledger_ids.update(str(item).strip() for item in decision_ids)

        ranges = section.get("source_ranges")
        if not isinstance(ranges, list) or not ranges:
            findings.append(Finding("ERROR", "EVIDENCE_RANGE", f"「{section['draft_heading']}」缺少 source_ranges。", str(path)))
            continue
        for source_range in ranges:
            start = source_range.get("start") if isinstance(source_range, dict) else None
            end = source_range.get("end") if isinstance(source_range, dict) else None
            if start not in source_timestamps or end not in source_timestamps:
                findings.append(
                    Finding(
                        "ERROR",
                        "EVIDENCE_TIMECODE",
                        f"「{section['draft_heading']}」的时间段 {start}–{end} 未完整出现在原稿。",
                        str(path),
                    )
                )
            elif time_to_seconds(start) > time_to_seconds(end):
                findings.append(
                    Finding(
                        "ERROR",
                        "EVIDENCE_ORDER",
                        f"「{section['draft_heading']}」的证据时间段顺序错误：{start}–{end}。",
                        str(path),
                    )
                )
            elif long_interview and time_to_seconds(end) - time_to_seconds(start) > 20 * 60:
                findings.append(
                    Finding(
                        "ERROR",
                        "EVIDENCE_RANGE_TOO_BROAD",
                        f"「{section['draft_heading']}」的单段证据跨度超过20分钟：{start}–{end}。",
                        str(path),
                    )
                )
            else:
                start_seconds = time_to_seconds(start)
                end_seconds = time_to_seconds(end)
                evidence_intervals.append((start_seconds, end_seconds))
                if previous_module_start is not None and start_seconds < previous_module_start:
                    findings.append(
                        Finding(
                            "ERROR",
                            "EVIDENCE_MODULE_ORDER",
                            "证据映射没有按原直播时间顺序排列。",
                            str(path),
                        )
                    )
                previous_module_start = start_seconds
    if long_interview and ledger_ids - mapped_ledger_ids:
        findings.append(
            Finding(
                "ERROR",
                "EVIDENCE_UNMAPPED_EDITS",
                f"以下非普通编辑尚未关联到语义模块：{sorted(ledger_ids - mapped_ledger_ids)}",
                str(path),
            )
        )
    if long_interview:
        for draft_heading in draft_sections:
            if normalize_text(draft_heading) not in evidence_headings:
                findings.append(
                    Finding(
                        "ERROR",
                        "EVIDENCE_UNMAPPED_HEADING",
                        f"发布章节“{draft_heading}”没有对应证据模块。",
                        str(path),
                    )
                )

        merged: list[list[int]] = []
        for start_seconds, end_seconds in sorted(evidence_intervals):
            if not merged or start_seconds > merged[-1][1] + 120:
                merged.append([start_seconds, end_seconds])
            else:
                merged[-1][1] = max(merged[-1][1], end_seconds)
        for previous, current in zip(merged, merged[1:]):
            if current[0] - previous[1] > 15 * 60:
                findings.append(
                    Finding(
                        "ERROR",
                        "EVIDENCE_TIMELINE_GAP",
                        "证据映射存在超过15分钟的未解释时间空档。",
                        str(path),
                    )
                )
                break

        if coverage:
            for line in coverage.splitlines():
                if not line.lstrip().startswith("|"):
                    continue
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if len(cells) < 9 or cells[5] not in {"A", "B"}:
                    continue
                timestamps = TIMESTAMP_RE.findall(cells[0])
                if len(timestamps) != 2:
                    continue
                coverage_start = time_to_seconds(timestamps[0])
                coverage_end = time_to_seconds(timestamps[1])
                coverage_duration = max(1, coverage_end - coverage_start)
                allowed_slack = min(60, max(2, int(coverage_duration * 0.05)))
                covered = any(
                    interval_start <= coverage_start + allowed_slack
                    and interval_end >= coverage_end - allowed_slack
                    for interval_start, interval_end in merged
                )
                if not covered:
                    findings.append(
                        Finding(
                            "ERROR",
                            "EVIDENCE_COVERAGE_DRIFT",
                            f"覆盖表A/B模块 {cells[0]} 没有落入证据映射的时间范围。",
                            str(path),
                        )
                    )
    return findings


def combine_support(paths: dict[str, Path]) -> str:
    parts = [read_text(paths["source"])]
    # The global glossary proves spelling, not that a term belongs in this
    # interview. Presence must still be supported by the source, case sheet, or
    # supplied background material.
    for key in ("terms", "background"):
        if paths[key].is_file():
            parts.append(read_text(paths[key]))
    return "\n".join(parts)


def audit_dual_track_package_evidence(
    case_dir: Path,
    source: str,
    draft: str,
    report: str,
    path: Path,
) -> list[Finding]:
    comparison = re.search(r"^- 对比结论：\s*(.+)$", report, re.MULTILINE)
    if not comparison or "AI优于人工" not in comparison.group(1):
        return []

    try:
        from summarize_dual_track_review import summarize_package
    except ImportError:
        return [
            Finding(
                "ERROR",
                "MISSING_DUAL_TRACK_PACKAGE_EVIDENCE",
                "无法加载双轨评审汇总器，不能验证AI优于人工的声明。",
                str(path),
            )
        ]

    source_path = case_dir / "inputs" / SOURCE_NAME
    draft_path = case_dir / "outputs" / DRAFT_NAME
    source_hash = file_sha256(source_path) if source_path.is_file() else ""
    draft_hash = file_sha256(draft_path) if draft_path.is_file() else ""
    package_dirs = sorted((case_dir / "_work").glob("*双轨匿名评审包*"))
    problems: list[str] = []
    for package_dir in package_dirs:
        manifest_path = package_dir / "manifest.json"
        if not manifest_path.is_file():
            problems.append(f"{package_dir.name}:缺manifest")
            continue
        try:
            manifest = json.loads(read_text(manifest_path))
            result = summarize_package(package_dir)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            problems.append(f"{package_dir.name}:{error}")
            continue
        drafts = manifest.get("drafts", {})
        first = drafts.get("first", {}) if isinstance(drafts, dict) else {}
        roles = manifest.get("track_roles", {})
        hashes_match = (
            manifest.get("source_sha256") == source_hash
            and isinstance(first, dict)
            and first.get("sha256") == draft_hash
        )
        primary_is_ai = isinstance(roles, dict) and roles.get("first") == "AI主要稿"
        campaign_bound = False
        campaign_path_text = manifest.get("campaign_manifest")
        if isinstance(campaign_path_text, str):
            campaign_path = Path(campaign_path_text)
            if campaign_path.is_file():
                campaign_bound = (
                    hashlib.sha256(read_text(campaign_path).encode("utf-8")).hexdigest()
                    == manifest.get("campaign_manifest_sha256")
                )
        input_bundle_bound = False
        try:
            from freeze_input_bundle import verify_input_bundle

            input_bundle = verify_input_bundle(case_dir)
            input_bundle_bound = (
                input_bundle.get("bundle_sha256")
                == manifest.get("input_bundle_sha256")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        valid_experiment = (
            manifest.get("experiment_type") == "双轨对比"
            and manifest.get("reference_exposure") == "未在生成阶段使用对照稿"
            and isinstance(manifest.get("input_bundle_sha256"), str)
            and len(manifest["input_bundle_sha256"]) == 64
            and isinstance(manifest.get("campaign_manifest_sha256"), str)
            and len(manifest["campaign_manifest_sha256"]) == 64
            and campaign_bound
            and input_bundle_bound
        )
        if (
            hashes_match
            and primary_is_ai
            and valid_experiment
            and result.get("evidence_ready") is True
            and result.get("single_case_primary_superior") is True
        ):
            return []
        problems.append(f"{package_dir.name}:哈希、评审闭环或主要稿胜出条件不满足")

    detail = "；".join(problems[:5]) if problems else "未找到双轨匿名评审包"
    return [
        Finding(
            "ERROR",
            "MISSING_DUAL_TRACK_PACKAGE_EVIDENCE",
            "声称AI优于人工必须绑定当前原稿和当前AI稿的真实匿名评审包，且来源评分、读者评分、总分、单项差和最终偏好均满足主要稿胜出。"
            + detail,
            str(path),
        )
    ]


def audit_case(case_dir: Path) -> list[Finding]:
    case_dir = case_dir.resolve()
    paths, findings = check_required_files(case_dir)
    if any(finding.level == "ERROR" and finding.code == "MISSING_FILE" for finding in findings):
        return findings

    source = read_text(paths["source"])
    draft = read_text(paths["draft"])
    issues = read_text(paths["issues"])
    report = read_text(paths["report"])
    evidence = read_text(paths["evidence"])
    coverage = read_text(paths["coverage"]) if paths["coverage"].is_file() else ""
    raw_full_draft = read_text(paths["full_draft"]) if paths["full_draft"].is_file() else ""
    scope = read_content_scope(case_dir, source, raw_full_draft)
    findings.extend(Finding("ERROR", "CONTENT_SCOPE_INVALID", message, str(paths['edit_ledger'])) for message in scope.errors)
    findings.extend(Finding("WARNING", "CONTENT_SCOPE_PENDING", message, str(paths['edit_ledger'])) for message in scope.warnings)
    analysis = None
    try:
        analysis = build_speaker_analysis(case_dir, source)
    except ValueError as exc:
        findings.append(Finding("ERROR", "SPEAKER_ANALYSIS_UNAVAILABLE", str(exc), str(paths['speaker_timeline'])))
    analysis_source = scope.apply_to_analysis(analysis.source) if analysis is not None else scope.source
    analysis_turn_count = analysis.count_turns(declared_semantic_end_seconds(coverage), source=analysis_source) if analysis is not None else None
    support = combine_support(paths)
    term_sources = [
        (paths[key], read_text(paths[key]))
        for key in ("global_terms", "terms")
        if paths[key].is_file()
    ]

    findings.extend(audit_source(analysis.source if analysis is not None else source, paths["source"]))
    if all(
        paths[key].is_file()
        for key in ("speaker_calibration", "speaker_timeline", "speaker_conflicts")
    ):
        findings.extend(
            audit_speaker_preflight(
                source,
                issues,
                read_text(paths["speaker_calibration"]),
                read_text(paths["speaker_timeline"]),
                read_text(paths["speaker_conflicts"]),
                paths["speaker_calibration"],
                paths["speaker_timeline"],
                paths["speaker_conflicts"],
            )
        )
    findings.extend(audit_draft(draft, support, paths["draft"], grounding_source=analysis_source))
    findings.extend(audit_proper_nouns(draft, term_sources, paths["draft"]))
    findings.extend(audit_retention(analysis_source, draft, paths["draft"], coverage=coverage, source_turn_count=analysis_turn_count))
    if paths["full_draft"].is_file():
        findings.extend(
            audit_full_draft(
                scope.full_draft,
                draft,
                paths["full_draft"],
            )
        )
    if paths["coverage"].is_file():
        findings.extend(audit_coverage_map(coverage, source, paths["coverage"]))
    if paths["edit_ledger"].is_file():
        edit_ledger = read_text(paths["edit_ledger"])
        findings.extend(
            audit_edit_ledger(
                edit_ledger,
                source,
                issues,
                draft,
                paths["edit_ledger"],
                speaker_analysis=analysis,
            )
        )
        try:
            analysis_ledger = analysis.with_ledger(edit_ledger) if analysis is not None else edit_ledger
        except ValueError:
            analysis_ledger = edit_ledger  # audit_edit_ledger already reports the invalid JSON.
        findings.extend(audit_speaker_attribution(analysis_source, draft, analysis_ledger, paths["draft"]))
        findings.extend(
            audit_suspicious_speaker_sandwiches(analysis_source, analysis_ledger, paths["source"])
        )
        findings.extend(audit_source_order(analysis_source, draft, analysis_ledger, paths["draft"]))
        findings.extend(
            audit_timeline_retention(
                analysis_source,
                draft,
                analysis_ledger,
                paths["draft"],
                coverage=coverage,
            )
        )
        findings.extend(
            audit_speaker_retention_balance(analysis_source, draft, analysis_ledger, paths["draft"])
        )
    findings.extend(audit_title_number_inventory(draft, issues, paths["issues"]))
    findings.extend(audit_issue_list(issues, paths["issues"], source=source))
    findings.extend(
        audit_review_sheet(
            issues,
            source,
            paths["review_sheet"],
            replay_exists=paths["replay"].is_file(),
        )
    )
    findings.extend(
        audit_report(
            report,
            paths["report"],
            ratio=retention_ratio(
                analysis_source,
                draft,
                source_end_seconds=declared_semantic_end_seconds(coverage),
            ),
            draft=draft,
        )
    )
    findings.extend(
        audit_dual_track_package_evidence(
            case_dir,
            source,
            draft,
            report,
            paths["report"],
        )
    )
    findings.extend(
        audit_evidence(
            evidence,
            source,
            draft,
            paths["evidence"],
            edit_ledger=read_text(paths["edit_ledger"]) if paths["edit_ledger"].is_file() else None,
            coverage=read_text(paths["coverage"]) if paths["coverage"].is_file() else None,
        )
    )
    return findings


def parse_duration_minutes(value: str) -> float | None:
    hours = re.search(r"(\d+(?:\.\d+)?)\s*小时", value)
    minutes = re.search(r"(\d+(?:\.\d+)?)\s*分钟", value)
    if not hours and not minutes:
        return None
    return (float(hours.group(1)) * 60 if hours else 0) + (
        float(minutes.group(1)) if minutes else 0
    )


def audit_human_review_metrics(case_dir: Path, report: str) -> list[Finding]:
    baseline_dir = case_dir / "_work/终审基线"
    manifest_path = baseline_dir / "manifest.json"
    baseline_path = baseline_dir / "01_AI交付稿.md"
    metrics_path = case_dir / "_work/人工终审修改量.json"
    report_path = case_dir / "outputs" / REPORT_NAME
    source_path = case_dir / "inputs" / SOURCE_NAME
    draft_path = case_dir / "outputs" / DRAFT_NAME
    findings: list[Finding] = []
    if not manifest_path.is_file() or not baseline_path.is_file():
        findings.append(
            Finding(
                "ERROR",
                "RELEASE_MISSING_REVIEW_BASELINE",
                "发布前必须保留真人修改前的AI交付稿和哈希manifest，不能事后凭印象估算人工修改量。",
                str(baseline_dir),
            )
        )
        return findings
    if not metrics_path.is_file():
        findings.append(
            Finding(
                "ERROR",
                "RELEASE_MISSING_HUMAN_EDIT_METRICS",
                "发布前必须生成 `_work/人工终审修改量.json`，记录真实终审人、耗时和AI稿到终稿的差异。",
                str(metrics_path),
            )
        )
        return findings
    try:
        manifest = json.loads(read_text(manifest_path))
        metrics = json.loads(read_text(metrics_path))
    except (OSError, json.JSONDecodeError) as error:
        return [
            Finding(
                "ERROR",
                "RELEASE_REVIEW_METRICS_INVALID",
                f"终审修改量文件无法解析：{error}",
                str(metrics_path),
            )
        ]
    current_hashes = {
        "source": file_sha256(source_path) if source_path.is_file() else "",
        "baseline": file_sha256(baseline_path),
        "draft": file_sha256(draft_path) if draft_path.is_file() else "",
    }
    hash_fields_match = (
        manifest.get("source_sha256") == current_hashes["source"]
        and manifest.get("draft_sha256") == current_hashes["baseline"]
        and metrics.get("source_sha256") == current_hashes["source"]
        and metrics.get("baseline_draft_sha256") == current_hashes["baseline"]
        and metrics.get("final_draft_sha256") == current_hashes["draft"]
    )
    rate = metrics.get("human_modification_rate")
    minutes = metrics.get("human_review_minutes")
    if not valid_review_date(metrics.get("review_date")):
        findings.append(Finding("ERROR", "RELEASE_REVIEW_DATE_INVALID", "终审统计中的日期不是有效的YYYY-MM-DD日历日期。", str(metrics_path)))
    if (
        not hash_fields_match
        or not isinstance(rate, (int, float))
        or not math.isfinite(float(rate))
        or not 0 <= float(rate) <= 1
        or not isinstance(minutes, (int, float))
        or not math.isfinite(float(minutes))
        or float(minutes) <= 0
    ):
        findings.append(
            Finding(
                "ERROR",
                "RELEASE_REVIEW_METRICS_DRIFT",
                "终审基线、原稿、当前终稿或修改量统计不一致；必须基于当前文件重新生成，不能复用旧结果。",
                str(metrics_path),
            )
        )
        return findings

    reviewer = re.search(r"^- 终审人：\s*(.+)$", report, re.MULTILINE)
    review_date = re.search(r"^- 终审日期：\s*(.+)$", report, re.MULTILINE)
    if review_date and not valid_review_date(review_date.group(1).strip()):
        findings.append(Finding("ERROR", "RELEASE_REVIEW_DATE_INVALID", "质检报告中的终审日期无效。", str(report_path)))
    duration = re.search(r"^- 人工终审耗时：\s*(.+)$", report, re.MULTILINE)
    report_minutes = parse_duration_minutes(duration.group(1)) if duration else None
    if (
        not reviewer
        or reviewer.group(1).strip() != str(metrics.get("reviewer", "")).strip()
        or not review_date
        or review_date.group(1).strip() != str(metrics.get("review_date", "")).strip()
        or report_minutes is None
        or abs(report_minutes - float(minutes)) > 0.5
    ):
        findings.append(
            Finding(
                "ERROR",
                "RELEASE_REVIEW_METRICS_REPORT_DRIFT",
                "质检报告中的终审人、日期或人工耗时与修改量证据不一致。",
                str(report_path),
            )
        )
    return findings


def audit_release_readiness(case_dir: Path, findings: list[Finding]) -> list[Finding]:
    """Apply publication-only gates after the reusable package audit passes."""
    case_dir = case_dir.resolve()
    report_path = case_dir / "outputs" / REPORT_NAME
    draft_path = case_dir / "outputs" / DRAFT_NAME
    report = read_text(report_path) if report_path.is_file() else ""
    draft = read_text(draft_path) if draft_path.is_file() else ""
    release_findings: list[Finding] = []
    source_path = case_dir/'inputs'/SOURCE_NAME
    full_path = case_dir/'_work'/FULL_DRAFT_NAME
    if source_path.is_file() and full_path.is_file():
        scope = read_content_scope(case_dir, read_text(source_path), read_text(full_path), handoff=True)
        release_findings.extend(Finding('ERROR','CONTENT_SCOPE_INVALID',message,str(case_dir/'_work'/EDIT_LEDGER_NAME)) for message in scope.errors)

    if any(item.code in {"MISSING_REPLAY", "SPEAKER_PREFLIGHT_UNAVAILABLE"} for item in findings):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_REPLAY_MISSING",
                "发布审计必须有直播回放；仅凭转写稿无法确认说话人归属和ASR疑点。",
                str(case_dir / "inputs"),
            )
        )
    speaker_preflight_paths = [
        case_dir / "_work" / SPEAKER_CALIBRATION_NAME,
        case_dir / "_work" / SPEAKER_TIMELINE_NAME,
        case_dir / "_work" / SPEAKER_CONFLICTS_NAME,
    ]
    if not all(path.is_file() for path in speaker_preflight_paths):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_SPEAKER_PREFLIGHT_MISSING",
                "发布审计要求完整的说话人校准、独立时间轴和冲突清单。",
                str(case_dir / "_work"),
            )
        )
    if any(item.code == "SPEAKER_CONFLICT_OPEN" for item in findings):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_OPEN_SPEAKER_CONFLICT",
                "发布审计不允许存在未关闭的说话人冲突、混合发言或无法映射块。",
                str(case_dir / "_work" / SPEAKER_CONFLICTS_NAME),
            )
        )
    if any(item.code == "SPEAKER_CONFLICT_WEAK_CLOSURE" for item in findings):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_WEAK_SPEAKER_CLOSURE",
                "发布审计不接受仅靠上下文或问答逻辑关闭的说话人冲突；必须回听、用独立声纹/画面证据确认，或删除对归属有依赖的片段。",
                str(case_dir / "_work" / SPEAKER_CONFLICTS_NAME),
            )
        )

    if any(item.code == "UNRESOLVED_P1" for item in findings):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_OPEN_P1",
                "发布审计不允许存在未关闭P1；必须回听、当事人确认、删除或采用已记录的最终保守表达。",
                str(case_dir / "outputs" / ISSUES_NAME),
            )
        )

    publishable = re.search(r"^- 是否可发布：\s*(.+)$", report, re.MULTILINE)
    publish_status = publishable.group(1).strip() if publishable else ""
    if publish_status not in {"是", "可发布"}:
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_NOT_APPROVED",
                f"发布审计要求质检状态为“可发布”，当前为“{publish_status or '未填写'}”。",
                str(report_path),
            )
        )

    score_kind = re.search(r"^- 评分性质：\s*(.+)$", report, re.MULTILINE)
    score_kind_value = score_kind.group(1).strip() if score_kind else ""
    if score_kind_value not in {"人工终审", "独立双轨评审"}:
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_NO_HUMAN_REVIEW",
                "发布审计要求评分性质为人工终审或独立双轨评审，AI预评不能放行。",
                str(report_path),
            )
        )
    else:
        duration_fields = {}
        for label in ("人工终审耗时", "总耗时"):
            match = re.search(rf"^- {label}：\s*(.+)$", report, re.MULTILINE)
            duration_fields[label] = (
                parse_duration_minutes(match.group(1).strip()) if match else None
            )
        if any(value is None for value in duration_fields.values()):
            release_findings.append(
                Finding(
                    "ERROR",
                    "RELEASE_MISSING_REVIEW_DURATION",
                    "人工终审或双轨评审完成后，必须以分钟或小时记录人工终审耗时和总耗时。",
                    str(report_path),
                )
            )

    readthrough = re.search(r"^- 发布稿独立通读：\s*(.+)$", report, re.MULTILINE)
    if (
        not readthrough
        or readthrough.group(1).strip() not in {"已完成", "通过", "真人已完成"}
    ):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_PUBLISH_DRAFT_NOT_READ",
                "发布模式要求真人从导语到结尾独立通读发布稿；完整底稿或AI自检不能替代。",
                str(report_path),
            )
        )

    reviewer = re.search(r"^- 终审人：\s*(.+)$", report, re.MULTILINE)
    review_date = re.search(r"^- 终审日期：\s*(.+)$", report, re.MULTILINE)
    invalid_values = {"", "待填", "待填写", "无"}
    if (
        not reviewer
        or reviewer.group(1).strip() in invalid_values
        or not review_date
        or review_date.group(1).strip() in invalid_values
        or not valid_review_date(review_date.group(1).strip())
    ):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_MISSING_SIGNOFF",
                "发布审计要求填写终审人和有效的YYYY-MM-DD终审日期。",
                str(report_path),
            )
        )

    if re.search(r"运营素材占位|\[图片\]|\[视频\]|\[二维码\]", draft):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_PLACEHOLDER",
                "发布稿仍有图片、视频、二维码或运营素材占位，必须补齐真实素材或删除占位。",
                str(draft_path),
            )
        )

    grounding_warning = any(item.code in {"GROUNDING_REVIEW", "LOW_GROUNDING"} for item in findings)
    grounding_review = re.search(r"^- 来源贴近度人工复核：\s*(.+)$", report, re.MULTILINE)
    if grounding_warning and (
        not grounding_review or grounding_review.group(1).strip() not in {"已完成", "通过"}
    ):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_GROUNDING_UNREVIEWED",
                "存在来源贴近度警告，发布前必须逐段人工对照并在质检报告标记“来源贴近度人工复核：已完成”。",
                str(report_path),
            )
        )
    near_duplicate_warning = any(
        item.code == "NEAR_DUPLICATE_PARAGRAPH" for item in findings
    )
    near_duplicate_review = re.search(r"^- 近义重复人工复核：\s*(.+)$", report, re.MULTILINE)
    if near_duplicate_warning and (
        not near_duplicate_review
        or near_duplicate_review.group(1).strip() not in {"已完成", "通过"}
    ):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_NEAR_DUPLICATE_UNREVIEWED",
                "存在近义重复提醒；发布前必须判断是否属于有效复述，并在质检报告标记“近义重复人工复核：已完成”。",
                str(report_path),
            )
        )
    speaker_review_warning = any(
        item.code == "LOW_CONFIDENCE_SPEAKER_ATTRIBUTION" for item in findings
    )
    speaker_review = re.search(
        r"^- 低置信度说话人归属复核：\s*(.+)$", report, re.MULTILINE
    )
    if speaker_review_warning and (
        not speaker_review or speaker_review.group(1).strip() not in {"已完成", "通过"}
    ):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_SPEAKER_ATTRIBUTION_UNREVIEWED",
                "存在低置信度说话人归属提醒；发布前必须逐段对照并记录复核完成。",
                str(report_path),
            )
        )
    speaker_samples = re.search(r"^- 说话人随机抽查：\s*(.+)$", report, re.MULTILINE)
    if (
        not speaker_samples
        or speaker_samples.group(1).strip() not in {"已完成", "通过", "真人已完成"}
    ):
        release_findings.append(
            Finding(
                "ERROR",
                "RELEASE_SPEAKER_SAMPLES_MISSING",
                "发布模式要求从全场五个时段随机抽查说话人归属，并在质检报告标记“说话人随机抽查：已完成”。",
                str(report_path),
            )
        )
    release_findings.extend(audit_human_review_metrics(case_dir, report))
    return release_findings


def summarize(findings: Iterable[Finding]) -> dict[str, int]:
    result = {"ERROR": 0, "WARNING": 0}
    for finding in findings:
        result[finding.level] = result.get(finding.level, 0) + 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="审计双人直播专访的发布稿、待核清单、质检报告和证据映射。"
    )
    parser.add_argument("case_dir", type=Path, help="包含 inputs/outputs/_work 的单场任务目录")
    parser.add_argument("--json", type=Path, dest="json_path", help="将机读结果写入 JSON 文件")
    parser.add_argument("--strict", action="store_true", help="将 WARNING 也视为非通过")
    parser.add_argument("--release", action="store_true", help="启用发布门禁：要求P1关闭、人工签字、无占位和必要的来源复核")
    args = parser.parse_args()

    if not args.case_dir.is_dir():
        print(f"ERROR: 任务目录不存在：{args.case_dir}", file=sys.stderr)
        return 2

    findings = audit_case(args.case_dir)
    if args.release:
        findings.extend(audit_release_readiness(args.case_dir, findings))
    counts = summarize(findings)
    failed = counts["ERROR"] > 0 or (args.strict and counts["WARNING"] > 0)
    print(f"AUDIT={'FAIL' if failed else 'PASS'} errors={counts['ERROR']} warnings={counts['WARNING']}")
    for finding in findings:
        location = f" {finding.file}" if finding.file else ""
        print(f"[{finding.level}] {finding.code}{location}: {finding.message}")

    if args.json_path:
        payload = {
            "status": "FAIL" if failed else "PASS",
            "errors": counts["ERROR"],
            "warnings": counts["WARNING"],
            "findings": [asdict(finding) for finding in findings],
        }
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
