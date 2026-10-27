#!/usr/bin/env python3
"""Summarize completed anonymous A/B review forms without inventing a winner."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import re
from pathlib import Path


CRITERIA = (
    "更像在听直播",
    "更愿意读完",
    "人物更鲜活",
    "长但不拖沓",
    "更适合直接发布",
    "最终偏好",
)
SOURCE_SCORE_DIMENSIONS = {
    "忠实与可追溯（35）": 35.0,
    "原始内容完整度（30）": 30.0,
}
READER_SCORE_DIMENSIONS = {
    "对谈现场与人物原声（20）": 20.0,
    "阅读导航与节奏（10）": 10.0,
    "排版与运营完成度（5）": 5.0,
}
SOURCE_BLOCKERS = (
    "编造事实",
    "说话人归属错误",
    "A级内容静默遗漏",
    "未标记低置信度内容",
    "已确认专名错写",
)


def field(text: str, label: str) -> str:
    match = re.search(rf"^- {re.escape(label)}：\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def table_values(text: str, label: str) -> tuple[str, str] | None:
    match = re.search(
        rf"^\|\s*{re.escape(label)}\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
        text,
        re.MULTILINE,
    )
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def weighted_table_values(text: str, label: str) -> tuple[str, str] | None:
    match = re.search(
        rf"^\|\s*{re.escape(label)}\s*\|\s*[^|]+?\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
        text,
        re.MULTILINE,
    )
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def parse_score(value: str, maximum: float) -> float | None:
    try:
        score = float(value)
    except ValueError:
        return None
    if not 0 <= score <= maximum:
        return None
    return score


def summarize_package(package_dir: Path) -> dict[str, object]:
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺少双轨manifest：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    mapping = manifest.get("private_mapping")
    if not isinstance(mapping, dict) or set(mapping) != {"A", "B"}:
        raise ValueError("manifest缺少完整A/B映射")

    completed_readers: list[str] = []
    reviewer_codes: set[str] = set()
    invalid_forms: list[str] = []
    raw_counts = {criterion: Counter() for criterion in CRITERIA}
    reader_score_values = {
        label: {dimension: [] for dimension in READER_SCORE_DIMENSIONS}
        for label in ("A", "B")
    }
    for reader_dir in sorted(package_dir.glob("R[0-9][0-9]")):
        form = reader_dir / "03_匿名评审表.md"
        if not form.is_file():
            invalid_forms.append(f"{reader_dir.name}:缺表")
            continue
        text = form.read_text(encoding="utf-8-sig")
        if field(text, "评审状态") != "已完成":
            continue
        choices = {criterion: field(text, criterion) for criterion in CRITERIA}
        invalid = [key for key, value in choices.items() if value not in {"A", "B", "持平"}]
        form_scores: dict[str, dict[str, float]] = {"A": {}, "B": {}}
        for dimension, maximum in READER_SCORE_DIMENSIONS.items():
            values = weighted_table_values(text, dimension)
            if not values:
                invalid.append(dimension)
                continue
            for label, value in zip(("A", "B"), values):
                score = parse_score(value, maximum)
                if score is None:
                    invalid.append(f"{dimension}-{label}")
                else:
                    form_scores[label][dimension] = score
        reviewer_code = field(text, "评审人代号")
        review_date = field(text, "评审日期")
        if reviewer_code in {"", "待填", "待填写"}:
            invalid.append("评审人代号")
        elif reviewer_code in reviewer_codes:
            invalid.append("评审人代号重复")
        if review_date in {"", "待填", "待填写"}:
            invalid.append("评审日期")
        if invalid:
            invalid_forms.append(f"{reader_dir.name}:{','.join(invalid)}")
            continue
        reviewer_codes.add(reviewer_code)
        completed_readers.append(reader_dir.name)
        for criterion, choice in choices.items():
            raw_counts[criterion][choice] += 1
        for label in ("A", "B"):
            for dimension, score in form_scores[label].items():
                reader_score_values[label][dimension].append(score)

    source_form = package_dir / "来源评审/来源评审表.md"
    source_text = source_form.read_text(encoding="utf-8-sig") if source_form.is_file() else ""
    source_complete = field(source_text, "评审状态") == "已完成"
    input_consistent = field(source_text, "输入一致性") == "是"
    source_reviewer = field(source_text, "来源评审人")
    source_review_date = field(source_text, "评审日期")
    source_metadata_complete = (
        source_reviewer not in {"", "待填", "待填写"}
        and source_review_date not in {"", "待填", "待填写"}
    )
    source_allows_comparison = (
        field(source_text, "是否允许进入读者体验总分比较") == "是"
    )
    source_form_errors: list[str] = []
    source_blockers: dict[str, list[str]] = {"A": [], "B": []}
    for blocker in SOURCE_BLOCKERS:
        values = table_values(source_text, blocker)
        if not values:
            source_form_errors.append(blocker)
            continue
        for label, value in zip(("A", "B"), values):
            if value not in {"0", "有"}:
                source_form_errors.append(f"{blocker}-{label}")
            elif value == "有":
                source_blockers[label].append(blocker)
    source_scores_by_label: dict[str, dict[str, float]] = {"A": {}, "B": {}}
    for dimension, maximum in SOURCE_SCORE_DIMENSIONS.items():
        values = table_values(source_text, dimension)
        if not values:
            source_form_errors.append(dimension)
            continue
        for label, value in zip(("A", "B"), values):
            score = parse_score(value, maximum)
            if score is None:
                source_form_errors.append(f"{dimension}-{label}")
            else:
                source_scores_by_label[label][dimension] = score
    source_form_valid = not source_form_errors
    source_blocker_free = not any(source_blockers.values())
    evidence_ready = (
        len(completed_readers) >= 3
        and not invalid_forms
        and source_complete
        and input_consistent
        and source_metadata_complete
        and source_form_valid
        and source_blocker_free
        and source_allows_comparison
    )

    translated: dict[str, dict[str, int]] = {}
    for criterion, counts in raw_counts.items():
        translated[criterion] = {
            str(mapping["A"]): counts["A"],
            str(mapping["B"]): counts["B"],
            "tie": counts["持平"],
        }
    preference = translated["最终偏好"]
    first_votes = preference.get("first", 0)
    second_votes = preference.get("second", 0)
    reader_average_by_track: dict[str, dict[str, float]] = {}
    source_scores_by_track: dict[str, dict[str, float]] = {}
    dimension_scores_by_track: dict[str, dict[str, float]] = {}
    total_scores: dict[str, float] = {}
    for label, track in mapping.items():
        reader_average_by_track[str(track)] = {
            dimension: sum(values) / len(values)
            for dimension, values in reader_score_values[label].items()
            if values
        }
        source_scores_by_track[str(track)] = source_scores_by_label[label]
        dimension_scores_by_track[str(track)] = {
            **source_scores_by_label[label],
            **reader_average_by_track[str(track)],
        }
        total_scores[str(track)] = sum(dimension_scores_by_track[str(track)].values())

    if not evidence_ready:
        winner = "证据未闭环"
    elif total_scores.get("first", 0) > total_scores.get("second", 0):
        winner = "first"
    elif total_scores.get("second", 0) > total_scores.get("first", 0):
        winner = "second"
    else:
        winner = "持平"

    if first_votes > second_votes:
        preference_winner = "first"
    elif second_votes > first_votes:
        preference_winner = "second"
    else:
        preference_winner = "持平"
    primary_dimension_gap_ok = evidence_ready and all(
        dimension_scores_by_track.get("first", {}).get(dimension, 0)
        >= dimension_scores_by_track.get("second", {}).get(dimension, 0) - 3
        for dimension in (*SOURCE_SCORE_DIMENSIONS, *READER_SCORE_DIMENSIONS)
    )
    single_case_primary_superior = bool(
        evidence_ready
        and winner == "first"
        and preference_winner == "first"
        and primary_dimension_gap_ok
    )

    return {
        "version": 1,
        "case": manifest.get("case", package_dir.parent.name),
        "completed_readers": completed_readers,
        "valid_reader_count": len(completed_readers),
        "invalid_forms": invalid_forms,
        "source_review_complete": source_complete,
        "input_consistent": input_consistent,
        "source_metadata_complete": source_metadata_complete,
        "source_allows_comparison": source_allows_comparison,
        "source_form_valid": source_form_valid,
        "source_form_errors": source_form_errors,
        "source_blockers": {
            str(mapping[label]): values for label, values in source_blockers.items()
        },
        "evidence_ready": evidence_ready,
        "votes_by_track": translated,
        "source_scores_by_track": source_scores_by_track,
        "reader_average_scores_by_track": reader_average_by_track,
        "dimension_scores_by_track": dimension_scores_by_track,
        "total_scores": total_scores,
        "winner": winner,
        "preference_winner": preference_winner,
        "primary_dimension_gap_ok": primary_dimension_gap_ok,
        "single_case_primary_superior": single_case_primary_superior,
        "mapping": mapping,
    }


def render_summary(result: dict[str, object]) -> str:
    lines = [
        "# 双轨匿名评审结果",
        "",
        f"- 场次：{result['case']}",
        f"- 有效读者：{result['valid_reader_count']}",
        f"- 来源评审完成：{'是' if result['source_review_complete'] else '否'}",
        f"- 输入一致：{'是' if result['input_consistent'] else '否'}",
        f"- 来源评审人及日期完整：{'是' if result['source_metadata_complete'] else '否'}",
        f"- 来源评分及阻断项填写有效：{'是' if result['source_form_valid'] else '否'}",
        f"- 来源评审允许比较：{'是' if result['source_allows_comparison'] else '否'}",
        f"- 证据闭环：{'是' if result['evidence_ready'] else '否'}",
        f"- 同尺度总分胜出：{result['winner']}",
        f"- 读者最终偏好胜出：{result['preference_winner']}",
        f"- 主要稿单场优于对照：{'是' if result['single_case_primary_superior'] else '否'}",
        "",
        "| 指标 | first | second | 持平 |",
        "| --- | ---: | ---: | ---: |",
    ]
    votes = result["votes_by_track"]
    for criterion in CRITERIA:
        item = votes[criterion]
        lines.append(
            f"| {criterion} | {item.get('first', 0)} | {item.get('second', 0)} | {item.get('tie', 0)} |"
        )
    lines.extend(
        [
            "",
            "| 同尺度评分 | first | second |",
            "| --- | ---: | ---: |",
        ]
    )
    dimensions = result["dimension_scores_by_track"]
    for dimension in (*SOURCE_SCORE_DIMENSIONS, *READER_SCORE_DIMENSIONS):
        lines.append(
            f"| {dimension} | {dimensions.get('first', {}).get(dimension, 0):.2f} | "
            f"{dimensions.get('second', {}).get(dimension, 0):.2f} |"
        )
    totals = result["total_scores"]
    lines.append(
        f"| 总分 | {totals.get('first', 0):.2f} | {totals.get('second', 0):.2f} |"
    )
    if result["source_form_errors"]:
        lines.extend(["", "## 来源评审表无效项", ""])
        lines.extend(f"- {item}" for item in result["source_form_errors"])
    blockers = result["source_blockers"]
    if any(blockers.values()):
        lines.extend(["", "## 来源发布阻断", ""])
        for track, items in blockers.items():
            for item in items:
                lines.append(f"- {track}：{item}")
    if result["invalid_forms"]:
        lines.extend(["", "## 无效表", ""])
        lines.extend(f"- {item}" for item in result["invalid_forms"])
    lines.extend(
        [
            "",
            "> first/second与作者的对应关系只在本目录manifest中保存。证据未闭环时，不得据此宣称AI优于人工。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="汇总同输入双轨匿名评审结果。")
    parser.add_argument("package_dir", type=Path)
    args = parser.parse_args()
    package_dir = args.package_dir.resolve()
    result = summarize_package(package_dir)
    (package_dir / "双轨评审结果.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (package_dir / "双轨评审结果.md").write_text(
        render_summary(result), encoding="utf-8"
    )
    status = "PASS" if result["evidence_ready"] else "INCOMPLETE"
    print(
        f"DUAL_TRACK_SUMMARY={status} readers={result['valid_reader_count']} "
        f"winner={result['winner']}"
    )
    return 0 if result["evidence_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
