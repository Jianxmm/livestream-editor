#!/usr/bin/env python3
"""Record comparable human effort and direct cost for both production tracks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from audit_draft import file_sha256


JSON_NAME = "双轨生产投入.json"
MARKDOWN_NAME = "双轨生产投入.md"


def _positive_number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label}必须是大于0的数字。")
    return float(value)


def _nonnegative_number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label}必须是大于等于0的数字。")
    return float(value)


def _validate_identity(value: str, label: str) -> str:
    clean = value.strip()
    if not clean or re.search(r"待|未知|unknown|todo", clean, re.IGNORECASE):
        raise ValueError(f"{label}必须填写真实值。")
    return clean


def _validate_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError("记录日期必须使用YYYY-MM-DD。") from error
    return value


def _case_dir_from_manifest(manifest: dict[str, object]) -> Path:
    source = manifest.get("source")
    if not isinstance(source, str):
        raise ValueError("评审包manifest缺原稿路径。")
    source_path = Path(source).resolve()
    if source_path.name != "02_带时间码原稿.md" or len(source_path.parents) < 2:
        raise ValueError("无法从评审包manifest定位标准场次目录。")
    return source_path.parents[1]


def record_production_effort(
    package_dir: Path,
    *,
    ai_total_human_minutes: float,
    human_team_person_minutes: float,
    ai_direct_cost: float,
    human_team_direct_cost: float,
    recorder: str,
    record_date: str,
    human_team_editors: int = 1,
    currency: str = "CNY",
) -> dict[str, object]:
    package_dir = package_dir.resolve()
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺评审包manifest：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if (
        manifest.get("experiment_type") != "双轨对比"
        or manifest.get("reference_exposure") != "未在生成阶段使用对照稿"
    ):
        raise ValueError("生产投入只能记录到未暴露对照稿的真正双轨评审包。")

    ai_minutes = _positive_number(ai_total_human_minutes, "AI轨总人工人分钟")
    human_minutes = _positive_number(human_team_person_minutes, "人工团队轨总人工人分钟")
    ai_cost = _nonnegative_number(ai_direct_cost, "AI轨直接费用")
    human_cost = _nonnegative_number(human_team_direct_cost, "人工团队轨直接费用")
    if not isinstance(human_team_editors, int) or isinstance(human_team_editors, bool) or human_team_editors <= 0:
        raise ValueError("人工团队参与人数必须是正整数。")
    recorder = _validate_identity(recorder, "记录人")
    record_date = _validate_date(record_date)
    currency = _validate_identity(currency, "币种")

    drafts = manifest.get("drafts")
    if not isinstance(drafts, dict):
        raise ValueError("评审包manifest缺双轨稿件哈希。")
    first = drafts.get("first")
    second = drafts.get("second")
    if not isinstance(first, dict) or not isinstance(second, dict):
        raise ValueError("评审包manifest缺双轨稿件哈希。")
    first_sha = first.get("sha256")
    second_sha = second.get("sha256")
    if not isinstance(first_sha, str) or not first_sha or not isinstance(second_sha, str) or not second_sha:
        raise ValueError("评审包manifest缺双轨稿件哈希。")

    case_dir = _case_dir_from_manifest(manifest)
    review_metrics_path = case_dir / "_work/人工终审修改量.json"
    if not review_metrics_path.is_file():
        raise FileNotFoundError(f"缺AI轨真人终审记录：{review_metrics_path}")
    review_metrics = json.loads(review_metrics_path.read_text(encoding="utf-8-sig"))
    review_minutes = _positive_number(
        review_metrics.get("human_review_minutes"), "AI轨真人终审分钟"
    )
    if review_metrics.get("final_draft_sha256") != first_sha:
        raise ValueError("AI轨真人终审记录未绑定当前评审包主要稿。")
    if review_metrics.get("source_sha256") != manifest.get("source_sha256"):
        raise ValueError("AI轨真人终审记录未绑定当前原稿。")
    if ai_minutes < review_minutes:
        raise ValueError("AI轨总人工人分钟不能小于其中已记录的真人终审分钟。")

    result: dict[str, object] = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "case": manifest.get("case"),
        "package": str(package_dir),
        "package_manifest_sha256": file_sha256(manifest_path),
        "campaign_manifest_sha256": manifest.get("campaign_manifest_sha256"),
        "input_bundle_sha256": manifest.get("input_bundle_sha256"),
        "source_sha256": manifest.get("source_sha256"),
        "ai_draft_sha256": first_sha,
        "human_team_draft_sha256": second_sha,
        "recorder": recorder,
        "record_date": record_date,
        "currency": currency,
        "ai_total_human_person_minutes": ai_minutes,
        "ai_final_review_minutes": review_minutes,
        "ai_direct_cost": ai_cost,
        "human_team_person_minutes": human_minutes,
        "human_team_editors": human_team_editors,
        "human_team_direct_cost": human_cost,
        "person_minutes_saved": human_minutes - ai_minutes,
        "direct_cost_saved": human_cost - ai_cost,
    }

    json_path = package_dir / JSON_NAME
    markdown_path = package_dir / MARKDOWN_NAME
    if json_path.exists() or markdown_path.exists():
        if not json_path.is_file():
            raise FileExistsError(f"生产投入记录已存在且不完整：{package_dir}")
        existing = json.loads(json_path.read_text(encoding="utf-8-sig"))
        comparable_keys = set(result) - {"created_at"}
        if all(existing.get(key) == result.get(key) for key in comparable_keys):
            return existing
        raise FileExistsError("生产投入记录已存在且内容不同；为保留实验留痕，不允许覆盖。")

    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(render_production_effort(result), encoding="utf-8")
    return result


def render_production_effort(result: dict[str, object]) -> str:
    return "\n".join(
        [
            "# 双轨生产投入记录",
            "",
            f"- 场次：{result['case']}",
            f"- 记录人：{result['recorder']}",
            f"- 记录日期：{result['record_date']}",
            f"- 币种：{result['currency']}",
            "",
            "| 轨道 | 总人工投入（人分钟） | 直接费用 | 备注 |",
            "| --- | ---: | ---: | --- |",
            f"| AI整理＋真人终审 | {result['ai_total_human_person_minutes']:.2f} | {result['ai_direct_cost']:.2f} | 其中真人终审{result['ai_final_review_minutes']:.2f}分钟 |",
            f"| 人工团队全流程 | {result['human_team_person_minutes']:.2f} | {result['human_team_direct_cost']:.2f} | {result['human_team_editors']}人参与，按人分钟累计 |",
            "",
            f"- 节省人分钟：{result['person_minutes_saved']:.2f}",
            f"- 节省直接费用：{result['direct_cost_saved']:.2f}",
            "",
            "> 人分钟是所有参与者实际投入时间之和，不是自然等待时长；两轨费用必须使用同一币种和同一成本口径。",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="记录AI轨和人工团队轨的真实生产投入。")
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("--ai-total-human-minutes", type=float, required=True)
    parser.add_argument("--human-team-person-minutes", type=float, required=True)
    parser.add_argument("--ai-direct-cost", type=float, required=True)
    parser.add_argument("--human-team-direct-cost", type=float, required=True)
    parser.add_argument("--human-team-editors", type=int, default=1)
    parser.add_argument("--recorder", required=True)
    parser.add_argument("--record-date", required=True)
    parser.add_argument("--currency", default="CNY")
    args = parser.parse_args()
    result = record_production_effort(
        args.package_dir,
        ai_total_human_minutes=args.ai_total_human_minutes,
        human_team_person_minutes=args.human_team_person_minutes,
        ai_direct_cost=args.ai_direct_cost,
        human_team_direct_cost=args.human_team_direct_cost,
        human_team_editors=args.human_team_editors,
        recorder=args.recorder,
        record_date=args.record_date,
        currency=args.currency,
    )
    print(
        "DUAL_TRACK_PRODUCTION=RECORDED "
        f"ai_person_minutes={result['ai_total_human_person_minutes']:.2f} "
        f"human_person_minutes={result['human_team_person_minutes']:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
