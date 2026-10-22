#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import json
import hashlib
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from register_validation_campaign import register_campaign
from record_dual_track_production import record_production_effort
from summarize_validation_campaign import DIMENSIONS, summarize_campaign


def completed_result(*, first_total: float = 94, second_total: float = 91) -> dict[str, object]:
    first_dimensions = {
        "忠实与可追溯（35）": 33.0,
        "原始内容完整度（30）": 28.0,
        "对谈现场与人物原声（20）": 19.0,
        "阅读导航与节奏（10）": 9.0,
        "排版与运营完成度（5）": 5.0,
    }
    second_dimensions = {
        "忠实与可追溯（35）": 32.0,
        "原始内容完整度（30）": 27.0,
        "对谈现场与人物原声（20）": 18.0,
        "阅读导航与节奏（10）": 9.0,
        "排版与运营完成度（5）": 5.0,
    }
    return {
        "evidence_ready": True,
        "dimension_scores_by_track": {
            "first": first_dimensions,
            "second": second_dimensions,
        },
        "total_scores": {"first": first_total, "second": second_total},
        "winner": "first" if first_total > second_total else "second",
        "votes_by_track": {
            "最终偏好": {"first": 3, "second": 1, "tie": 0}
        },
    }


class ValidationCampaignTest(unittest.TestCase):
    def make_packages(self, root: Path, count: int) -> list[Path]:
        packages: list[Path] = []
        for index in range(count):
            case_dir = root / f"case-data-{index + 1}"
            (case_dir / "inputs").mkdir(parents=True)
            source_path = case_dir / "inputs/02_带时间码原稿.md"
            source_text = f"原稿{index + 1}"
            source_path.write_text(source_text, encoding="utf-8")
            (case_dir / "inputs/01_直播回放.mp4").write_bytes(
                f"video-{index + 1}".encode("utf-8")
            )
            package = root / f"package-{index + 1}"
            package.mkdir()
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "case": case_dir.name,
                        "source_sha256": hashlib.sha256(
                            source_text.encode("utf-8")
                        ).hexdigest(),
                        "source": str(source_path),
                        "drafts": {
                            "first": {"sha256": f"draft-{index + 1}"},
                            "second": {"sha256": f"comparison-{index + 1}"},
                        },
                        "track_roles": {
                            "first": "AI主要稿",
                            "second": "人工团队或对照稿",
                        },
                        "experiment_type": "双轨对比",
                        "reference_exposure": "未在生成阶段使用对照稿",
                    }
                ),
                encoding="utf-8",
            )
            packages.append(package)
        return packages

    def add_human_metrics(self, packages: list[Path], minutes: list[float]) -> None:
        for package, duration in zip(packages, minutes):
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            source_path = Path(manifest["source"])
            work_dir = source_path.parents[1] / "_work"
            work_dir.mkdir(exist_ok=True)
            (work_dir / "人工终审修改量.json").write_text(
                json.dumps(
                    {
                        "source_sha256": manifest["source_sha256"],
                        "baseline_draft_sha256": "a" * 64,
                        "final_draft_sha256": manifest["drafts"]["first"]["sha256"],
                        "human_review_minutes": duration,
                        "human_modification_rate": 0.08,
                        "reviewer": "编辑甲",
                        "review_date": "2026-07-21",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    def add_production_metrics(
        self,
        packages: list[Path],
        ai_minutes: list[float],
        human_minutes: list[float],
    ) -> None:
        for package, ai_duration, human_duration in zip(
            packages, ai_minutes, human_minutes
        ):
            record_production_effort(
                package,
                ai_total_human_minutes=ai_duration,
                human_team_person_minutes=human_duration,
                ai_direct_cost=20,
                human_team_direct_cost=500,
                human_team_editors=2,
                recorder="项目负责人",
                record_date="2026-07-21",
            )

    def register_packages(self, root: Path, packages: list[Path]) -> Path:
        case_dirs = [
            Path(json.loads((package / "manifest.json").read_text(encoding="utf-8"))["source"]).parents[1]
            for package in packages
        ]
        output = root / "campaign-registration"
        register_campaign(case_dirs, output, "测试批次")
        campaign_path = output / "validation_campaign_manifest.json"
        campaign_hash = hashlib.sha256(
            campaign_path.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
        for package in packages:
            manifest_path = package / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            case_dir = Path(manifest["source"]).parents[1]
            bundle = json.loads(
                (case_dir / "_work/双轨输入清单.json").read_text(encoding="utf-8")
            )
            manifest["input_bundle_sha256"] = bundle["bundle_sha256"]
            manifest["campaign_manifest_sha256"] = campaign_hash
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
        return campaign_path

    def test_registration_requires_three_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 2)
            case_dirs = [
                Path(json.loads((package / "manifest.json").read_text(encoding="utf-8"))["source"]).parents[1]
                for package in packages
            ]
            with self.assertRaises(ValueError):
                register_campaign(case_dirs, root / "registration", "测试批次")

    def test_campaign_requires_three_independent_samples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 3)
            campaign = self.register_packages(root, packages)
            with patch(
                "summarize_validation_campaign.summarize_package",
                side_effect=[completed_result(), completed_result()],
            ):
                result = summarize_campaign(packages[:2], campaign)
            self.assertFalse(result["evidence_ready"])
            self.assertFalse(result["stable_superiority_proven"])
            self.assertTrue(any("预注册样本缺失" in item for item in result["invalid_packages"]))

    def test_campaign_rejects_case_added_after_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 4)
            campaign = self.register_packages(root, packages[:3])
            with patch(
                "summarize_validation_campaign.summarize_package",
                side_effect=[completed_result()] * 4,
            ):
                result = summarize_campaign(packages, campaign)
            self.assertFalse(result["evidence_ready"])
            self.assertTrue(
                any("不在预注册验证批次中" in item for item in result["invalid_packages"])
            )

    def test_campaign_rejects_changed_input_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 3)
            campaign = self.register_packages(root, packages)
            manifest_path = packages[0] / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["input_bundle_sha256"] = "0" * 64
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            with patch(
                "summarize_validation_campaign.summarize_package",
                side_effect=[completed_result()] * 3,
            ):
                result = summarize_campaign(packages, campaign)
            self.assertFalse(result["evidence_ready"])
            self.assertTrue(
                any("完整输入包与预注册不一致" in item for item in result["invalid_packages"])
            )

    def test_campaign_proves_stable_primary_advantage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 3)
            campaign = self.register_packages(root, packages)
            with patch(
                "summarize_validation_campaign.summarize_package",
                side_effect=[completed_result(), completed_result(), completed_result()],
            ):
                result = summarize_campaign(packages, campaign)
            self.assertEqual(3, result["sample_count"])
            self.assertTrue(result["evidence_ready"])
            self.assertTrue(result["stable_superiority_proven"])
            self.assertFalse(result["release_evidence_ready"])
            self.assertFalse(result["production_evidence_ready"])
            self.assertFalse(result["workflow_replacement_ready"])
            self.assertEqual(set(DIMENSIONS), set(result["average_dimension_scores"]["first"]))

    def test_campaign_marks_replacement_ready_with_bound_review_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 3)
            campaign = self.register_packages(root, packages)
            self.add_human_metrics(packages, [22, 28, 30])
            self.add_production_metrics(packages, [40, 45, 50], [180, 200, 220])
            with patch(
                "summarize_validation_campaign.summarize_package",
                side_effect=[completed_result(), completed_result(), completed_result()],
            ), patch(
                "summarize_validation_campaign.audit_case", return_value=[]
            ), patch(
                "summarize_validation_campaign.audit_release_readiness", return_value=[]
            ):
                result = summarize_campaign(packages, campaign)
            self.assertTrue(result["stable_quality_superiority_proven"])
            self.assertTrue(result["efficiency_evidence_ready"])
            self.assertTrue(result["production_evidence_ready"])
            self.assertTrue(result["production_advantage_proven"])
            self.assertTrue(result["release_evidence_ready"])
            self.assertTrue(result["workflow_replacement_ready"])
            self.assertAlmostEqual(26.667, result["average_human_review_minutes"], places=3)

    def test_production_record_rejects_ai_total_below_final_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 3)
            self.register_packages(root, packages)
            self.add_human_metrics(packages, [30, 30, 30])
            with self.assertRaises(ValueError):
                record_production_effort(
                    packages[0],
                    ai_total_human_minutes=20,
                    human_team_person_minutes=180,
                    ai_direct_cost=20,
                    human_team_direct_cost=500,
                    recorder="项目负责人",
                    record_date="2026-07-21",
                )

    def test_quality_advantage_without_production_advantage_is_not_replacement_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 3)
            campaign = self.register_packages(root, packages)
            self.add_human_metrics(packages, [22, 28, 30])
            self.add_production_metrics(packages, [180, 190, 200], [150, 160, 170])
            with patch(
                "summarize_validation_campaign.summarize_package",
                side_effect=[completed_result(), completed_result(), completed_result()],
            ), patch(
                "summarize_validation_campaign.audit_case", return_value=[]
            ), patch(
                "summarize_validation_campaign.audit_release_readiness", return_value=[]
            ):
                result = summarize_campaign(packages, campaign)
            self.assertTrue(result["stable_quality_superiority_proven"])
            self.assertTrue(result["production_evidence_ready"])
            self.assertFalse(result["production_advantage_proven"])
            self.assertFalse(result["workflow_replacement_ready"])

    def test_mixed_production_currencies_block_production_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 3)
            campaign = self.register_packages(root, packages)
            self.add_human_metrics(packages, [22, 28, 30])
            self.add_production_metrics(packages, [40, 45, 50], [180, 200, 220])
            production_path = packages[0] / "双轨生产投入.json"
            production = json.loads(production_path.read_text(encoding="utf-8"))
            production["currency"] = "USD"
            production_path.write_text(
                json.dumps(production, ensure_ascii=False), encoding="utf-8"
            )
            with patch(
                "summarize_validation_campaign.summarize_package",
                side_effect=[completed_result(), completed_result(), completed_result()],
            ):
                result = summarize_campaign(packages, campaign)
            self.assertFalse(result["production_evidence_ready"])
            self.assertTrue(
                any("不同币种" in item for item in result["invalid_production_evidence"])
            )

    def test_average_savings_cannot_hide_single_case_production_regression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 3)
            campaign = self.register_packages(root, packages)
            self.add_human_metrics(packages, [22, 28, 30])
            self.add_production_metrics(packages, [50, 50, 250], [100, 100, 200])
            with patch(
                "summarize_validation_campaign.summarize_package",
                side_effect=[completed_result(), completed_result(), completed_result()],
            ):
                result = summarize_campaign(packages, campaign)
            self.assertTrue(result["production_evidence_ready"])
            self.assertLess(
                result["average_ai_total_human_person_minutes"],
                result["average_human_team_person_minutes"],
            )
            self.assertEqual(2, result["production_time_win_cases"])
            self.assertEqual(1, len(result["production_case_regressions"]))
            self.assertFalse(result["production_advantage_proven"])

    def test_campaign_rejects_single_dimension_gap_over_three(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 3)
            campaign = self.register_packages(root, packages)
            weak = completed_result(first_total=94, second_total=91)
            weak["dimension_scores_by_track"]["first"]["阅读导航与节奏（10）"] = 5.0
            weak["dimension_scores_by_track"]["second"]["阅读导航与节奏（10）"] = 9.0
            with patch(
                "summarize_validation_campaign.summarize_package",
                side_effect=[completed_result(), weak, completed_result()],
            ):
                result = summarize_campaign(packages, campaign)
            self.assertTrue(result["evidence_ready"])
            self.assertFalse(result["stable_superiority_proven"])
            self.assertEqual(1, len(result["per_case_dimension_gaps_below_minus_3"]))

    def test_campaign_rejects_known_benchmark_regression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = self.make_packages(root, 3)
            campaign = self.register_packages(root, packages)
            manifest_path = packages[0] / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["experiment_type"] = "已知标杆回归"
            manifest["reference_exposure"] = "生成阶段已见对照稿"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            with patch(
                "summarize_validation_campaign.summarize_package",
                side_effect=[completed_result(), completed_result(), completed_result()],
            ):
                result = summarize_campaign(packages, campaign)
            self.assertFalse(result["evidence_ready"])
            self.assertFalse(result["stable_superiority_proven"])
            self.assertEqual(2, result["sample_count"])
            self.assertTrue(
                any("不是未暴露对照稿的真正双轨样本" in item for item in result["invalid_packages"])
            )


if __name__ == "__main__":
    unittest.main()
