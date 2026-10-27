#!/usr/bin/env python3
"""Aggregate at least three completed dual-track reviews into one proof result."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from audit_draft import audit_case, audit_release_readiness, file_sha256
from summarize_dual_track_review import (
    READER_SCORE_DIMENSIONS,
    SOURCE_SCORE_DIMENSIONS,
    summarize_package,
)


DIMENSIONS = (*SOURCE_SCORE_DIMENSIONS, *READER_SCORE_DIMENSIONS)


def summarize_campaign(
    package_dirs: list[Path], campaign_manifest_path: Path
) -> dict[str, object]:
    campaign_manifest_path = campaign_manifest_path.resolve()
    if not campaign_manifest_path.is_file():
        raise FileNotFoundError(f"缺验证批次预注册manifest：{campaign_manifest_path}")
    campaign_text = campaign_manifest_path.read_text(encoding="utf-8-sig")
    campaign = json.loads(campaign_text)
    planned = campaign.get("samples")
    if not isinstance(planned, list) or len(planned) < 3:
        raise ValueError("验证批次必须在结果产生前预注册至少3场。")
    expected_by_hash: dict[str, str] = {}
    expected_bundle_by_hash: dict[str, str] = {}
    expected_cases: set[str] = set()
    for sample in planned:
        if not isinstance(sample, dict):
            raise ValueError("验证批次样本格式无效。")
        case = str(sample.get("case", ""))
        source_hash = str(sample.get("source_sha256", ""))
        input_bundle_hash = str(sample.get("input_bundle_sha256", ""))
        if (
            not case
            or not source_hash
            or not input_bundle_hash
            or case in expected_cases
            or source_hash in expected_by_hash
        ):
            raise ValueError("验证批次包含缺失或重复的场次/原稿哈希。")
        expected_cases.add(case)
        expected_by_hash[source_hash] = case
        expected_bundle_by_hash[source_hash] = input_bundle_hash

    resolved = [path.resolve() for path in package_dirs]
    package_results: list[dict[str, object]] = []
    invalid_packages: list[str] = []
    invalid_efficiency_evidence: list[str] = []
    invalid_production_evidence: list[str] = []
    invalid_release_evidence: list[str] = []
    source_hashes: set[str] = set()
    cases: set[str] = set()
    submitted_hashes: set[str] = set()

    for package_dir in resolved:
        manifest_path = package_dir / "manifest.json"
        if not manifest_path.is_file():
            invalid_packages.append(f"{package_dir}:缺manifest")
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            result = summarize_package(package_dir)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            invalid_packages.append(f"{package_dir}:{error}")
            continue
        source_hash = str(manifest.get("source_sha256", ""))
        case = str(manifest.get("case", ""))
        roles = manifest.get("track_roles", {})
        if not source_hash or not case:
            invalid_packages.append(f"{package_dir}:缺场次或原稿哈希")
            continue
        if expected_by_hash.get(source_hash) != case:
            invalid_packages.append(f"{package_dir}:不在预注册验证批次中")
            continue
        if manifest.get("input_bundle_sha256") != expected_bundle_by_hash[source_hash]:
            invalid_packages.append(f"{package_dir}:完整输入包与预注册不一致")
            continue
        if manifest.get("campaign_manifest_sha256") != hashlib.sha256(
            campaign_text.encode("utf-8")
        ).hexdigest():
            invalid_packages.append(f"{package_dir}:评审包未绑定当前预注册manifest")
            continue
        submitted_hashes.add(source_hash)
        if source_hash in source_hashes or case in cases:
            invalid_packages.append(f"{package_dir}:与其他样本重复")
            continue
        source_hashes.add(source_hash)
        cases.add(case)
        if not isinstance(roles, dict) or roles.get("first") != "AI主要稿":
            invalid_packages.append(f"{package_dir}:主要稿角色不是AI")
            continue
        if (
            manifest.get("experiment_type") != "双轨对比"
            or manifest.get("reference_exposure") != "未在生成阶段使用对照稿"
        ):
            invalid_packages.append(f"{package_dir}:不是未暴露对照稿的真正双轨样本")
            continue
        if result.get("evidence_ready") is not True:
            invalid_packages.append(f"{package_dir}:单场证据未闭环")
            continue
        dimensions = result.get("dimension_scores_by_track", {})
        totals = result.get("total_scores", {})
        if not all(
            isinstance(dimensions, dict)
            and isinstance(dimensions.get(track), dict)
            and all(isinstance(dimensions[track].get(item), (int, float)) for item in DIMENSIONS)
            and isinstance(totals, dict)
            and isinstance(totals.get(track), (int, float))
            for track in ("first", "second")
        ):
            invalid_packages.append(f"{package_dir}:缺完整100分量表")
            continue

        human_metrics: dict[str, object] | None = None
        production_metrics: dict[str, object] | None = None
        release_ready = False
        source_path_text = manifest.get("source")
        first_draft = manifest.get("drafts", {}).get("first", {})
        if isinstance(source_path_text, str) and isinstance(first_draft, dict):
            source_path = Path(source_path_text)
            if source_path.name == "02_带时间码原稿.md" and len(source_path.parents) >= 2:
                case_dir = source_path.parents[1]
                metrics_path = case_dir / "_work/人工终审修改量.json"
                if metrics_path.is_file():
                    try:
                        candidate = json.loads(metrics_path.read_text(encoding="utf-8-sig"))
                    except (OSError, json.JSONDecodeError) as error:
                        invalid_efficiency_evidence.append(
                            f"{package_dir}:终审修改量文件无效:{error}"
                        )
                    else:
                        minutes = candidate.get("human_review_minutes")
                        modification_rate = candidate.get("human_modification_rate")
                        if (
                            candidate.get("final_draft_sha256")
                            == first_draft.get("sha256")
                            and candidate.get("source_sha256")
                            == manifest.get("source_sha256")
                            and isinstance(candidate.get("baseline_draft_sha256"), str)
                            and len(candidate["baseline_draft_sha256"]) == 64
                            and isinstance(minutes, (int, float))
                            and minutes > 0
                            and isinstance(modification_rate, (int, float))
                            and 0 <= modification_rate <= 1
                            and str(candidate.get("reviewer", "")).strip()
                            and str(candidate.get("review_date", "")).strip()
                        ):
                            human_metrics = candidate
                        else:
                            invalid_efficiency_evidence.append(
                                f"{package_dir}:终审修改量未与当前AI稿绑定或缺真实人工记录"
                            )
                else:
                    invalid_efficiency_evidence.append(f"{package_dir}:缺人工终审修改量")

                production_path = package_dir / "双轨生产投入.json"
                if production_path.is_file():
                    try:
                        candidate = json.loads(production_path.read_text(encoding="utf-8-sig"))
                    except (OSError, json.JSONDecodeError) as error:
                        invalid_production_evidence.append(
                            f"{package_dir}:双轨生产投入文件无效:{error}"
                        )
                    else:
                        ai_minutes = candidate.get("ai_total_human_person_minutes")
                        human_minutes = candidate.get("human_team_person_minutes")
                        ai_cost = candidate.get("ai_direct_cost")
                        human_cost = candidate.get("human_team_direct_cost")
                        review_minutes = (
                            human_metrics.get("human_review_minutes")
                            if isinstance(human_metrics, dict)
                            else None
                        )
                        if (
                            candidate.get("package_manifest_sha256") == file_sha256(manifest_path)
                            and candidate.get("campaign_manifest_sha256")
                            == manifest.get("campaign_manifest_sha256")
                            and candidate.get("input_bundle_sha256")
                            == manifest.get("input_bundle_sha256")
                            and candidate.get("source_sha256") == manifest.get("source_sha256")
                            and candidate.get("ai_draft_sha256") == first_draft.get("sha256")
                            and candidate.get("human_team_draft_sha256")
                            == manifest.get("drafts", {}).get("second", {}).get("sha256")
                            and isinstance(ai_minutes, (int, float))
                            and not isinstance(ai_minutes, bool)
                            and ai_minutes > 0
                            and isinstance(human_minutes, (int, float))
                            and not isinstance(human_minutes, bool)
                            and human_minutes > 0
                            and isinstance(ai_cost, (int, float))
                            and not isinstance(ai_cost, bool)
                            and ai_cost >= 0
                            and isinstance(human_cost, (int, float))
                            and not isinstance(human_cost, bool)
                            and human_cost >= 0
                            and isinstance(review_minutes, (int, float))
                            and ai_minutes >= review_minutes
                            and str(candidate.get("currency", "")).strip()
                            and str(candidate.get("recorder", "")).strip()
                            and str(candidate.get("record_date", "")).strip()
                        ):
                            production_metrics = candidate
                        else:
                            invalid_production_evidence.append(
                                f"{package_dir}:双轨生产投入未与当前评审包/终审记录绑定或数值无效"
                            )
                else:
                    invalid_production_evidence.append(f"{package_dir}:缺双轨生产投入记录")
                release_findings = audit_release_readiness(case_dir, audit_case(case_dir))
                if release_findings:
                    codes = ",".join(sorted({item.code for item in release_findings}))
                    invalid_release_evidence.append(
                        f"{package_dir}:发布门禁未通过:{codes}"
                    )
                else:
                    release_ready = True
            else:
                invalid_efficiency_evidence.append(f"{package_dir}:无法定位场次终审修改量")
                invalid_release_evidence.append(f"{package_dir}:无法定位场次发布门禁")
        else:
            invalid_efficiency_evidence.append(f"{package_dir}:缺原稿或AI稿路径")
            invalid_release_evidence.append(f"{package_dir}:缺场次路径")
        package_results.append(
            {
                "package": str(package_dir),
                "case": case,
                "result": result,
                "human_metrics": human_metrics,
                "production_metrics": production_metrics,
                "release_ready": release_ready,
            }
        )

    for missing_hash, missing_case in expected_by_hash.items():
        if missing_hash not in submitted_hashes:
            invalid_packages.append(f"预注册样本缺失:{missing_case}")

    evidence_ready = (
        len(package_results) == len(expected_by_hash)
        and len(package_results) >= 3
        and not invalid_packages
    )
    average_dimensions = {
        track: {
            dimension: (
                sum(item["result"]["dimension_scores_by_track"][track][dimension] for item in package_results)
                / len(package_results)
                if package_results
                else 0.0
            )
            for dimension in DIMENSIONS
        }
        for track in ("first", "second")
    }
    average_totals = {
        track: (
            sum(item["result"]["total_scores"][track] for item in package_results)
            / len(package_results)
            if package_results
            else 0.0
        )
        for track in ("first", "second")
    }
    case_wins = Counter(
        str(item["result"].get("winner", "证据未闭环")) for item in package_results
    )
    preference_votes = Counter()
    for item in package_results:
        preference = item["result"]["votes_by_track"]["最终偏好"]
        preference_votes["first"] += int(preference.get("first", 0))
        preference_votes["second"] += int(preference.get("second", 0))
        preference_votes["tie"] += int(preference.get("tie", 0))

    per_case_dimension_gaps: list[dict[str, object]] = []
    for item in package_results:
        dimensions = item["result"]["dimension_scores_by_track"]
        for dimension in DIMENSIONS:
            gap = float(dimensions["first"][dimension]) - float(
                dimensions["second"][dimension]
            )
            if gap < -3:
                per_case_dimension_gaps.append(
                    {"case": item["case"], "dimension": dimension, "gap": round(gap, 3)}
                )

    majority_needed = math.floor(len(package_results) / 2) + 1
    stable_quality_superiority_proven = bool(
        evidence_ready
        and average_totals["first"] > average_totals["second"]
        and case_wins["first"] >= majority_needed
        and preference_votes["first"] > preference_votes["second"]
        and not per_case_dimension_gaps
    )
    valid_human_metrics = [
        item["human_metrics"]
        for item in package_results
        if isinstance(item.get("human_metrics"), dict)
    ]
    efficiency_evidence_ready = bool(
        evidence_ready
        and len(valid_human_metrics) == len(package_results)
        and not invalid_efficiency_evidence
    )
    release_evidence_ready = bool(
        evidence_ready
        and all(item["release_ready"] is True for item in package_results)
        and not invalid_release_evidence
    )
    average_human_review_minutes = (
        sum(float(item["human_review_minutes"]) for item in valid_human_metrics)
        / len(valid_human_metrics)
        if valid_human_metrics
        else 0.0
    )
    maximum_human_review_minutes = (
        max(float(item["human_review_minutes"]) for item in valid_human_metrics)
        if valid_human_metrics
        else 0.0
    )
    average_human_modification_rate = (
        sum(float(item["human_modification_rate"]) for item in valid_human_metrics)
        / len(valid_human_metrics)
        if valid_human_metrics
        else 0.0
    )
    valid_production_metrics = [
        item["production_metrics"]
        for item in package_results
        if isinstance(item.get("production_metrics"), dict)
    ]
    production_currencies = {
        str(item["currency"]) for item in valid_production_metrics if item.get("currency")
    }
    if valid_production_metrics and len(production_currencies) != 1:
        invalid_production_evidence.append("验证批次的双轨生产投入使用了不同币种，不能汇总费用。")
    production_evidence_ready = bool(
        evidence_ready
        and len(valid_production_metrics) == len(package_results)
        and not invalid_production_evidence
    )
    average_ai_total_human_person_minutes = (
        sum(float(item["ai_total_human_person_minutes"]) for item in valid_production_metrics)
        / len(valid_production_metrics)
        if valid_production_metrics
        else 0.0
    )
    average_human_team_person_minutes = (
        sum(float(item["human_team_person_minutes"]) for item in valid_production_metrics)
        / len(valid_production_metrics)
        if valid_production_metrics
        else 0.0
    )
    average_ai_direct_cost = (
        sum(float(item["ai_direct_cost"]) for item in valid_production_metrics)
        / len(valid_production_metrics)
        if valid_production_metrics
        else 0.0
    )
    average_human_team_direct_cost = (
        sum(float(item["human_team_direct_cost"]) for item in valid_production_metrics)
        / len(valid_production_metrics)
        if valid_production_metrics
        else 0.0
    )
    production_case_regressions: list[dict[str, object]] = []
    production_time_wins = 0
    for item in package_results:
        metrics = item.get("production_metrics")
        if not isinstance(metrics, dict):
            continue
        ai_minutes = float(metrics["ai_total_human_person_minutes"])
        human_minutes = float(metrics["human_team_person_minutes"])
        ai_cost = float(metrics["ai_direct_cost"])
        human_cost = float(metrics["human_team_direct_cost"])
        if ai_minutes < human_minutes:
            production_time_wins += 1
        if ai_minutes > human_minutes or ai_cost > human_cost:
            production_case_regressions.append(
                {
                    "case": item["case"],
                    "ai_person_minutes": ai_minutes,
                    "human_team_person_minutes": human_minutes,
                    "ai_direct_cost": ai_cost,
                    "human_team_direct_cost": human_cost,
                }
            )
    production_advantage_proven = bool(
        production_evidence_ready
        and average_ai_total_human_person_minutes < average_human_team_person_minutes
        and average_ai_direct_cost <= average_human_team_direct_cost
        and production_time_wins >= majority_needed
        and not production_case_regressions
    )
    workflow_replacement_ready = bool(
        stable_quality_superiority_proven
        and efficiency_evidence_ready
        and production_advantage_proven
        and release_evidence_ready
        and average_human_review_minutes <= 30
        and maximum_human_review_minutes <= 45
    )
    return {
        "version": 1,
        "campaign_name": str(campaign.get("campaign_name", "")),
        "campaign_manifest": str(campaign_manifest_path),
        "campaign_manifest_sha256": hashlib.sha256(
            campaign_text.encode("utf-8")
        ).hexdigest(),
        "planned_sample_count": len(expected_by_hash),
        "sample_count": len(package_results),
        "cases": [item["case"] for item in package_results],
        "invalid_packages": invalid_packages,
        "invalid_efficiency_evidence": invalid_efficiency_evidence,
        "invalid_production_evidence": invalid_production_evidence,
        "invalid_release_evidence": invalid_release_evidence,
        "evidence_ready": evidence_ready,
        "average_dimension_scores": average_dimensions,
        "average_total_scores": average_totals,
        "case_wins": dict(case_wins),
        "preference_votes": dict(preference_votes),
        "per_case_dimension_gaps_below_minus_3": per_case_dimension_gaps,
        "stable_quality_superiority_proven": stable_quality_superiority_proven,
        "stable_superiority_proven": stable_quality_superiority_proven,
        "efficiency_evidence_ready": efficiency_evidence_ready,
        "production_evidence_ready": production_evidence_ready,
        "production_advantage_proven": production_advantage_proven,
        "production_currency": next(iter(production_currencies), ""),
        "production_time_win_cases": production_time_wins,
        "production_case_regressions": production_case_regressions,
        "release_evidence_ready": release_evidence_ready,
        "average_human_review_minutes": round(average_human_review_minutes, 3),
        "maximum_human_review_minutes": round(maximum_human_review_minutes, 3),
        "average_human_modification_rate": round(average_human_modification_rate, 6),
        "average_ai_total_human_person_minutes": round(
            average_ai_total_human_person_minutes, 3
        ),
        "average_human_team_person_minutes": round(
            average_human_team_person_minutes, 3
        ),
        "average_ai_direct_cost": round(average_ai_direct_cost, 3),
        "average_human_team_direct_cost": round(average_human_team_direct_cost, 3),
        "workflow_replacement_ready": workflow_replacement_ready,
    }


def render_campaign(result: dict[str, object]) -> str:
    lines = [
        "# 三场双轨验证汇总",
        "",
        f"- 预注册批次：{result['campaign_name']}",
        f"- 计划样本：{result['planned_sample_count']}",
        f"- 有效独立样本：{result['sample_count']}",
        f"- 证据闭环：{'是' if result['evidence_ready'] else '否'}",
        f"- 稳定质量优势已证明：{'是' if result['stable_quality_superiority_proven'] else '否'}",
        f"- AI终审效率证据闭环：{'是' if result['efficiency_evidence_ready'] else '否'}",
        f"- 双轨生产投入证据闭环：{'是' if result['production_evidence_ready'] else '否'}",
        f"- AI轨人力与费用优势：{'是' if result['production_advantage_proven'] else '否'}",
        f"- 三场发布门禁通过：{'是' if result['release_evidence_ready'] else '否'}",
        f"- 流程替代就绪：{'是' if result['workflow_replacement_ready'] else '否'}",
        f"- AI主要稿平均总分：{result['average_total_scores']['first']:.2f}",
        f"- 人工或对照稿平均总分：{result['average_total_scores']['second']:.2f}",
        f"- AI主要稿胜出场次：{result['case_wins'].get('first', 0)}",
        f"- 人工或对照稿胜出场次：{result['case_wins'].get('second', 0)}",
        f"- 最终偏好票：AI {result['preference_votes'].get('first', 0)} / 对照 {result['preference_votes'].get('second', 0)} / 持平 {result['preference_votes'].get('tie', 0)}",
        f"- 平均人工终审耗时：{result['average_human_review_minutes']:.2f}分钟",
        f"- 最长单场人工终审：{result['maximum_human_review_minutes']:.2f}分钟",
        f"- 平均人工修改率：{result['average_human_modification_rate']:.2%}",
        f"- AI轨平均总人工投入：{result['average_ai_total_human_person_minutes']:.2f}人分钟",
        f"- 人工团队轨平均总人工投入：{result['average_human_team_person_minutes']:.2f}人分钟",
        f"- AI轨人力胜出场次：{result['production_time_win_cases']}",
        f"- AI轨平均直接费用：{result['average_ai_direct_cost']:.2f} {result['production_currency']}",
        f"- 人工团队轨平均直接费用：{result['average_human_team_direct_cost']:.2f} {result['production_currency']}",
        "",
        "| 维度 | AI主要稿均分 | 人工或对照稿均分 |",
        "| --- | ---: | ---: |",
    ]
    for dimension in DIMENSIONS:
        lines.append(
            f"| {dimension} | {result['average_dimension_scores']['first'][dimension]:.2f} | "
            f"{result['average_dimension_scores']['second'][dimension]:.2f} |"
        )
    if result["per_case_dimension_gaps_below_minus_3"]:
        lines.extend(["", "## 单场单项落后超过3分", ""])
        for item in result["per_case_dimension_gaps_below_minus_3"]:
            lines.append(f"- {item['case']}｜{item['dimension']}｜差值{item['gap']}")
    if result["invalid_packages"]:
        lines.extend(["", "## 无效样本", ""])
        lines.extend(f"- {item}" for item in result["invalid_packages"])
    if result["invalid_efficiency_evidence"]:
        lines.extend(["", "## 缺失或无效的效率证据", ""])
        lines.extend(f"- {item}" for item in result["invalid_efficiency_evidence"])
    if result["invalid_production_evidence"]:
        lines.extend(["", "## 缺失或无效的双轨生产投入证据", ""])
        lines.extend(f"- {item}" for item in result["invalid_production_evidence"])
    if result["production_case_regressions"]:
        lines.extend(["", "## 单场生产投入回退", ""])
        for item in result["production_case_regressions"]:
            lines.append(
                f"- {item['case']}｜AI {item['ai_person_minutes']:.2f}人分钟/{item['ai_direct_cost']:.2f}，"
                f"人工 {item['human_team_person_minutes']:.2f}人分钟/{item['human_team_direct_cost']:.2f}"
            )
    if result["invalid_release_evidence"]:
        lines.extend(["", "## 未通过的单场发布门禁", ""])
        lines.extend(f"- {item}" for item in result["invalid_release_evidence"])
    lines.extend(
        [
            "",
            "> “稳定质量优势”只回答成稿质量；只有质量优势、修改量、终审耗时、同币种双轨生产投入与三场发布门禁同时闭环，AI轨至少多数场次节省人力且没有单场人力或费用回退，平均终审不超过30分钟、单场不超过45分钟，才能形成“流程替代就绪”结论。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="汇总至少3场同输入双轨匿名评审。")
    parser.add_argument("package_dirs", nargs="+", type=Path)
    parser.add_argument("--campaign-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = summarize_campaign(args.package_dirs, args.campaign_manifest)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "三场双轨验证结果.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "三场双轨验证结果.md").write_text(
        render_campaign(result), encoding="utf-8"
    )
    if not result["evidence_ready"]:
        status = "INCOMPLETE"
    elif result["workflow_replacement_ready"]:
        status = "REPLACEMENT_READY"
    elif result["stable_quality_superiority_proven"]:
        status = "QUALITY_ADVANTAGE"
    else:
        status = "COMPLETE_NO_ADVANTAGE"
    print(
        f"VALIDATION_CAMPAIGN={status} samples={result['sample_count']} "
        f"quality_superiority={str(result['stable_quality_superiority_proven']).lower()} "
        f"replacement_ready={str(result['workflow_replacement_ready']).lower()}"
    )
    return 0 if result["evidence_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
