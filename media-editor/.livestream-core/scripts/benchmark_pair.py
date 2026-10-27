#!/usr/bin/env python3
"""Measure the source/published benchmark pair without modifying either file."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import statistics
from pathlib import Path


SOURCE_TURN_RE = re.compile(
    r"^发言人([12])\s+(\d{2}:\d{2}(?::\d{2})?)\n(.*?)(?=^发言人[12]\s+\d{2}:\d{2}|\Z)",
    re.MULTILINE | re.DOTALL,
)
PUBLISHED_TURN_RE = re.compile(
    r"^\*\*([^*\n：:]{1,20})[：:]\*\*\s*(.*?)(?=^\*\*[^*\n：:]{1,20}[：:]\*\*|^## |^---$|\Z)",
    re.MULTILINE | re.DOTALL,
)
FILLERS = ("然后", "就是", "这个", "那个", "OK", "对对对", "评论区", "直播间", "关注")
HIGH_VALUE_CLOSE = ("想有钱先值钱", "重启的魄力", "前行也是前行")


def compact_lines(text: str) -> str:
    return " ".join(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("> [")
    )


def normalize_text(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", text)


def grounding_metrics(source: str, published: str) -> dict[str, float | int]:
    """Describe how close long body paragraphs remain to the transcript wording."""
    first_section = re.search(r"^##\s+", published, re.MULTILINE)
    body = published[first_section.start() :] if first_section else published
    source_clean = normalize_text(source)
    source_grams = {source_clean[index : index + 3] for index in range(max(0, len(source_clean) - 2))}
    values: list[float] = []
    for paragraph in re.split(r"\n\s*\n", body):
        stripped = paragraph.strip()
        if not stripped or stripped.startswith(("#", "> [", "[", "---")):
            continue
        stripped = re.sub(r"^\*\*[^*\n：:]{1,20}[：:]\*\*\s*", "", stripped)
        clean = normalize_text(stripped)
        if len(clean) < 40:
            continue
        grams = [clean[index : index + 3] for index in range(len(clean) - 2)]
        values.append(sum(gram in source_grams for gram in grams) / len(grams))
    if not values:
        return {"paragraphs": 0, "median": 0.0, "p10": 0.0, "below_25_share": 0.0}
    values.sort()
    return {
        "paragraphs": len(values),
        "median": round(statistics.median(values), 4),
        "p10": round(values[len(values) // 10], 4),
        "below_25_share": round(sum(value < 0.25 for value in values) / len(values), 4),
    }


def analyze_pair(source_path: Path, published_path: Path) -> dict[str, object]:
    source = source_path.read_text(encoding="utf-8-sig")
    published = published_path.read_text(encoding="utf-8-sig")
    source_turns = [
        (match.group(1), match.group(2), compact_lines(match.group(3)))
        for match in SOURCE_TURN_RE.finditer(source)
    ]
    all_published_turns = [
        (match.group(1), compact_lines(match.group(2)))
        for match in PUBLISHED_TURN_RE.finditer(published)
    ]
    main_speakers = {
        speaker for speaker, _ in Counter(speaker for speaker, _ in all_published_turns).most_common(2)
    }
    published_turns = [turn for turn in all_published_turns if turn[0] in main_speakers]
    source_chars = sum(len(turn[2]) for turn in source_turns)
    published_chars = sum(len(turn[1]) for turn in published_turns)
    published_tail = published[-2200:]
    source_tail = source[-3000:]
    return {
        "source_file": source_path.name,
        "published_file": published_path.name,
        "source_turns": len(source_turns),
        "published_turns": len(published_turns),
        "source_interview_chars": source_chars,
        "published_interview_chars": published_chars,
        "retention_ratio": round(published_chars / source_chars, 4) if source_chars else 0,
        "grounding": grounding_metrics(source, published),
        "filler_counts": {
            filler: {"source": source.count(filler), "published": published.count(filler)}
            for filler in FILLERS
        },
        "section_headings": re.findall(r"^## (.+)$", published, re.MULTILINE),
        "high_value_close": {
            phrase: {
                "present_in_source_tail": phrase in source_tail,
                "present_in_published_tail": phrase in published_tail,
            }
            for phrase in HIGH_VALUE_CLOSE
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="计算直播专访原稿与人工成稿的回归基线。")
    parser.add_argument("source", type=Path)
    parser.add_argument("published", type=Path)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()
    metrics = analyze_pair(args.source, args.published)
    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
    else:
        print(
            "BENCHMARK "
            f"source_turns={metrics['source_turns']} "
            f"published_turns={metrics['published_turns']} "
            f"source_chars={metrics['source_interview_chars']} "
            f"published_chars={metrics['published_interview_chars']} "
            f"retention={metrics['retention_ratio']:.2%} "
            f"grounding_median={metrics['grounding']['median']:.2%} "
            f"grounding_p10={metrics['grounding']['p10']:.2%}"
        )
        for filler, counts in metrics["filler_counts"].items():
            print(f"FILLER {filler}: {counts['source']} -> {counts['published']}")
        for phrase, presence in metrics["high_value_close"].items():
            print(
                f"CLOSE {phrase}: source_tail={presence['present_in_source_tail']} "
                f"published_tail={presence['present_in_published_tail']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
