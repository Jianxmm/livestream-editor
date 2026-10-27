#!/usr/bin/env python3
"""Freeze the AI handoff and measure the subsequent human final review."""

from __future__ import annotations

import argparse
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from route_livestream import file_sha256
from review_validation import valid_review_date


SOURCE = "inputs/02_带时间码原稿.md"
DRAFT = "outputs/01_公众号可发布稿.md"
BASELINE_DIR = "_work/终审基线"
BASELINE_DRAFT = "01_AI交付稿.md"
BASELINE_MANIFEST = "manifest.json"
METRICS_JSON = "_work/人工终审修改量.json"
METRICS_MD = "_work/人工终审修改量.md"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def non_whitespace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def paragraphs(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", item.strip()) for item in re.split(r"\n\s*\n", text) if item.strip()]


def freeze_baseline(case_dir: Path) -> dict[str, object]:
    case_dir = case_dir.resolve()
    source_path = case_dir / SOURCE
    draft_path = case_dir / DRAFT
    if not source_path.is_file() or not draft_path.is_file():
        raise FileNotFoundError("缺少带时间码原稿或公众号稿，不能冻结终审基线。")
    source = source_path.read_text(encoding="utf-8-sig")
    draft = draft_path.read_text(encoding="utf-8-sig")
    baseline_dir = case_dir / BASELINE_DIR
    manifest_path = baseline_dir / BASELINE_MANIFEST
    baseline_path = baseline_dir / BASELINE_DRAFT
    expected = {
        "source_sha256": file_sha256(source_path),
        "draft_sha256": file_sha256(draft_path),
    }
    if baseline_dir.exists():
        if manifest_path.is_file() and baseline_path.is_file():
            existing = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            if (
                existing.get("source_sha256") == expected["source_sha256"]
                and existing.get("draft_sha256") == expected["draft_sha256"]
                and file_sha256(baseline_path)
                == expected["draft_sha256"]
            ):
                return existing
        raise FileExistsError("终审基线已存在且与当前稿不同；不得覆盖历史AI交付稿。")

    baseline_dir.mkdir(parents=True)
    shutil.copy2(draft_path, baseline_path)
    manifest: dict[str, object] = {
        "version": 1,
        "case": case_dir.name,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": SOURCE,
        "source_sha256": expected["source_sha256"],
        "draft": DRAFT,
        "baseline_draft": f"{BASELINE_DIR}/{BASELINE_DRAFT}",
        "draft_sha256": expected["draft_sha256"],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def diff_counts(before: str, after: str) -> tuple[int, int, float]:
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    deleted = 0
    inserted = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        deleted += i2 - i1
        inserted += j2 - j1
    return deleted, inserted, matcher.ratio()


def summarize_human_review(
    case_dir: Path,
    reviewer: str,
    review_date: str,
    minutes: float,
    *,
    replace: bool = False,
) -> dict[str, object]:
    if not reviewer.strip() or reviewer.strip() in {"待填", "待填写"}:
        raise ValueError("必须填写真实终审人。")
    if not valid_review_date(review_date):
        raise ValueError("必须填写有效的YYYY-MM-DD终审日期。")
    if not math.isfinite(minutes) or minutes <= 0:
        raise ValueError("人工终审耗时必须大于0分钟。")

    case_dir = case_dir.resolve()
    source_path = case_dir / SOURCE
    draft_path = case_dir / DRAFT
    manifest_path = case_dir / BASELINE_DIR / BASELINE_MANIFEST
    baseline_path = case_dir / BASELINE_DIR / BASELINE_DRAFT
    for path in (source_path, draft_path, manifest_path, baseline_path):
        if not path.is_file():
            raise FileNotFoundError(f"缺少终审追踪文件：{path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    source = source_path.read_text(encoding="utf-8-sig")
    before = baseline_path.read_text(encoding="utf-8-sig")
    after = draft_path.read_text(encoding="utf-8-sig")
    if file_sha256(source_path) != manifest.get("source_sha256"):
        raise ValueError("原稿已在终审基线后变化，修改量不可比较。")
    if file_sha256(baseline_path) != manifest.get("draft_sha256"):
        raise ValueError("AI交付基线内容与manifest不一致。")

    before_clean = non_whitespace(before)
    after_clean = non_whitespace(after)
    deleted, inserted, similarity = diff_counts(before_clean, after_clean)
    before_paragraphs = paragraphs(before)
    after_paragraphs = paragraphs(after)
    paragraph_matcher = SequenceMatcher(
        None, before_paragraphs, after_paragraphs, autojunk=False
    )
    changed_paragraphs = sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in paragraph_matcher.get_opcodes()
        if tag != "equal"
    )
    final_hash = file_sha256(draft_path)
    result: dict[str, object] = {
        "version": 1,
        "case": case_dir.name,
        "reviewer": reviewer.strip(),
        "review_date": review_date.strip(),
        "human_review_minutes": float(minutes),
        "source_sha256": manifest["source_sha256"],
        "baseline_draft_sha256": manifest["draft_sha256"],
        "final_draft_sha256": final_hash,
        "baseline_non_whitespace_chars": len(before_clean),
        "final_non_whitespace_chars": len(after_clean),
        "character_similarity": round(similarity, 6),
        "human_modification_rate": round(1 - similarity, 6),
        "deleted_or_replaced_chars": deleted,
        "inserted_or_replaced_chars": inserted,
        "changed_paragraph_units": changed_paragraphs,
        "baseline_paragraphs": len(before_paragraphs),
        "final_paragraphs": len(after_paragraphs),
    }
    output_json = case_dir / METRICS_JSON
    output_md = case_dir / METRICS_MD
    if output_json.exists() and not replace:
        existing = json.loads(output_json.read_text(encoding="utf-8-sig"))
        if existing == result:
            return result
        raise FileExistsError("人工终审修改量已存在；确认需要重算时使用--replace。")
    output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_md.write_text(
        "\n".join(
            [
                "# 人工终审修改量",
                "",
                f"- 终审人：{result['reviewer']}",
                f"- 终审日期：{result['review_date']}",
                f"- 人工终审耗时：{result['human_review_minutes']:.1f}分钟",
                f"- AI交付稿字符：{result['baseline_non_whitespace_chars']}",
                f"- 最终稿字符：{result['final_non_whitespace_chars']}",
                f"- 字符相似度：{result['character_similarity']:.2%}",
                f"- 人工修改率：{result['human_modification_rate']:.2%}",
                f"- 删除或替换字符：{result['deleted_or_replaced_chars']}",
                f"- 新增或替换字符：{result['inserted_or_replaced_chars']}",
                f"- 涉及修改的段落单元：{result['changed_paragraph_units']}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="冻结AI交付稿并量化真人终审修改量。")
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze", help="真人修改前冻结AI交付基线")
    freeze.add_argument("case_dir", type=Path)
    summarize = subparsers.add_parser("summarize", help="真人终审后计算修改量")
    summarize.add_argument("case_dir", type=Path)
    summarize.add_argument("--reviewer", required=True)
    summarize.add_argument("--review-date", required=True)
    summarize.add_argument("--minutes", type=float, required=True)
    summarize.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze_baseline(args.case_dir)
        print(f"REVIEW_BASELINE=PASS case={result['case']} sha256={result['draft_sha256']}")
        return 0
    result = summarize_human_review(
        args.case_dir,
        args.reviewer,
        args.review_date,
        args.minutes,
        replace=args.replace,
    )
    print(
        f"HUMAN_REVIEW_METRICS=PASS case={result['case']} "
        f"modification_rate={result['human_modification_rate']:.2%}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
