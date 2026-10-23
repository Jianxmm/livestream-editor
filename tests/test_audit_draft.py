#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import json
import re
import tempfile
import unittest
from pathlib import Path

from audit_draft import (
    audit_draft,
    audit_case,
    audit_coverage_map,
    audit_evidence,
    audit_machine_transcripts,
    audit_release_readiness,
    audit_retention,
    audit_speaker_preflight,
    audit_source_order,
    audit_speaker_attribution,
    audit_speaker_retention_balance,
    audit_suspicious_speaker_sandwiches,
    audit_timeline_retention,
    conflicting_review_filenames,
    file_sha256,
    parse_speaker_conflict_rows,
    timeline_retention_profile,
)
from benchmark_pair import analyze_pair
from build_review_index import build_review_index
from extract_review_clips import (
    ReviewClip,
    install_staged_package,
    managed_clip_filenames,
    merge_overlapping_windows,
    remove_stale_managed_clips,
    select_stratified_samples,
)
from prepare_dual_track_review import prepare_review_package
from freeze_input_bundle import freeze_input_bundle, verify_input_bundle
from summarize_dual_track_review import summarize_package
from track_human_review import freeze_baseline, summarize_human_review


SOURCE = """发言人1   00:00
大家好，我是主持人。

发言人2   00:10
我是示例嘉宾，过去十年我一直在照顾家庭。

发言人1   00:30
你是怎么开始重启的？

发言人2   00:40
我先每天抽出一小时学习，一点点做出改变。

发言人1   01:00
这个经历对大家有什么启发？

发言人2   01:20
学习计划被打断不可怕，重要的是重新开始。
"""

DRAFT = """# 从困境里重新开始

最近，我和示例嘉宾在直播间做了一场关于重启人生的对谈。

内容比较多，你可以逐字阅读，也可以选择自己感兴趣的问题来看。

**以下文字版：**

**主持人：** 大家好，我是主持人。请你先介绍一下自己。

**示例嘉宾：** 我是示例嘉宾，过去十年我一直在照顾家庭。

## 01<br>过去的十年<br>她如何重新找回自己

**主持人：** 你是怎么开始重启的？

**示例嘉宾：** 过去十年，我一直在照顾家庭。后来我先每天抽出一小时学习，一点点做出改变。

## 02<br>计划会被打断<br>但人可以再次开始

**主持人：** 这个经历对大家有什么启发？

**示例嘉宾：** 学习计划被打断不可怕，重要的是重新开始。
"""

ISSUES = """# 待核问题清单

- P0：0
- P1：0
- P2：0

| 级别 | 时间码 | 问题类型 | 原文/成稿 | 处理建议 | 状态 |
| --- | --- | --- | --- | --- | --- |
"""

REPORT = """# 编辑质检报告

- 测试类型：无人工成稿独立整理
- 评分性质：AI预评
- 评审人：Codex（仅用于流程预检）
- 对比结论：未形成

| 维度 | 权重 | 得分 | 主要依据 |
| --- | ---: | ---: | --- |
| 忠实与可追溯 | 35 | 34 | 已核对 |
| 原始内容完整度 | 30 | 29 | 已核对 |
| 对谈现场与人物原声 | 20 | 19 | 已核对 |
| 阅读导航与节奏 | 10 | 9 | 已核对 |
| 排版与运营完成度 | 5 | 4 | 已核对 |

- AI 整理耗时：待记录
- 人工终审耗时：待执行
- 总耗时：待终审后填写
- 直接费用：0元
- 相比原工作流程节省时间：待实际终审后记录
- 发布稿独立通读：AI流程预检已完成
- 说话人随机抽查：待执行

- [x] 无编造事实或不可追溯数字
- [x] 无说话人归属错误

- 是否可发布：是
"""

EVIDENCE = {
    "version": 1,
    "source": "inputs/02_带时间码原稿.md",
    "sections": [
        {
            "draft_heading": "01 过去的十年",
            "source_ranges": [{"start": "00:10", "end": "00:40"}],
            "notes": "合并相邻问答",
        },
        {
            "draft_heading": "02 再次开始",
            "source_ranges": [{"start": "01:00", "end": "01:20"}],
            "notes": "保留结尾",
        },
    ],
}

EDIT_LEDGER = {
    "version": 1,
    "source": "inputs/02_带时间码原稿.md",
    "speaker_map": {"发言人1": "主持人", "发言人2": "示例嘉宾"},
    "nontrivial_edits": [],
}

SPEAKER_CALIBRATION = {
    "version": 1,
    "source_audio": "inputs/01_直播回放.mp4",
    "speaker_count": 2,
    "method": "单元测试独立时间轴",
    "status": "已确认",
    "source_label_default_map": {
        "发言人1": "SPK_HOST",
        "发言人2": "SPK_GUEST",
    },
    "speakers": [
        {
            "speaker_id": "SPK_HOST",
            "name": "主持人",
            "role": "主持人",
            "reference_segments": [
                {
                    "start": "00:00",
                    "end": "00:15",
                    "identity_basis": "回放画面与口型明确对应",
                    "overlap": False,
                    "review_status": "已确认",
                },
                {
                    "start": "00:30",
                    "end": "00:45",
                    "identity_basis": "真人听辨确认稳定声纹",
                    "overlap": False,
                    "review_status": "已确认",
                },
            ],
        },
        {
            "speaker_id": "SPK_GUEST",
            "name": "示例嘉宾",
            "role": "嘉宾",
            "reference_segments": [
                {
                    "start": "00:15",
                    "end": "00:30",
                    "identity_basis": "回放画面与口型明确对应",
                    "overlap": False,
                    "review_status": "已确认",
                },
                {
                    "start": "00:45",
                    "end": "01:00",
                    "identity_basis": "真人听辨确认稳定声纹",
                    "overlap": False,
                    "review_status": "已确认",
                },
            ],
        },
    ],
}

SPEAKER_TIMELINE = {
    "version": 1,
    "source_audio": "inputs/01_直播回放.mp4",
    "source_transcript": "inputs/02_带时间码原稿.md",
    "speaker_count": 2,
    "method": "单元测试独立说话人分离器v1",
    "calibration_file": "_work/说话人校准.json",
    "segments": [
        {"id": "S001", "start": "00:00", "end": "00:05", "raw_cluster": "A", "speaker_id": "SPK_HOST", "overlap": False},
        {"id": "S002", "start": "00:10", "end": "00:15", "raw_cluster": "B", "speaker_id": "SPK_GUEST", "overlap": False},
        {"id": "S003", "start": "00:30", "end": "00:35", "raw_cluster": "A", "speaker_id": "SPK_HOST", "overlap": False},
        {"id": "S004", "start": "00:40", "end": "00:45", "raw_cluster": "B", "speaker_id": "SPK_GUEST", "overlap": False},
        {"id": "S005", "start": "01:00", "end": "01:05", "raw_cluster": "A", "speaker_id": "SPK_HOST", "overlap": False},
        {"id": "S006", "start": "01:20", "end": "01:25", "raw_cluster": "B", "speaker_id": "SPK_GUEST", "overlap": False},
    ],
    "source_blocks": [
        {"source_start": "00:00", "source_label": "发言人1", "timeline_segment_ids": ["S001"], "resolved_speaker_id": "SPK_HOST", "status": "一致", "conflict_id": None, "notes": ""},
        {"source_start": "00:10", "source_label": "发言人2", "timeline_segment_ids": ["S002"], "resolved_speaker_id": "SPK_GUEST", "status": "一致", "conflict_id": None, "notes": ""},
        {"source_start": "00:30", "source_label": "发言人1", "timeline_segment_ids": ["S003"], "resolved_speaker_id": "SPK_HOST", "status": "一致", "conflict_id": None, "notes": ""},
        {"source_start": "00:40", "source_label": "发言人2", "timeline_segment_ids": ["S004"], "resolved_speaker_id": "SPK_GUEST", "status": "一致", "conflict_id": None, "notes": ""},
        {"source_start": "01:00", "source_label": "发言人1", "timeline_segment_ids": ["S005"], "resolved_speaker_id": "SPK_HOST", "status": "一致", "conflict_id": None, "notes": ""},
        {"source_start": "01:20", "source_label": "发言人2", "timeline_segment_ids": ["S006"], "resolved_speaker_id": "SPK_GUEST", "status": "一致", "conflict_id": None, "notes": ""},
    ],
}

SPEAKER_CONFLICTS = """# 说话人冲突清单

- 当前状态：已完成
- 未解决冲突：0

| 冲突编号 | 级别 | 时间码 | 冲突类型 | 原转写标签 | 独立时间轴 | 上下文提示 | 处理结论 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
"""


class AuditDraftTest(unittest.TestCase):
    def test_neutral_introduction_does_not_require_host_first_person(self) -> None:
        neutral = DRAFT.replace('最近，我和示例嘉宾在直播间做了一场关于重启人生的对谈。',
                                '这场直播由主持人与示例嘉宾讨论如何重启人生。')
        findings = audit_draft(neutral, '', Path('synthetic.md'))
        self.assertFalse(any(item.code in {'MISSING_HOST_FIRST_PERSON', 'MISSING_LIVE_CONTEXT', 'MISSING_READING_GUIDE'} for item in findings), findings)

    def make_case(self, root: Path) -> Path:
        (root / "inputs").mkdir()
        (root / "outputs").mkdir()
        (root / "_work").mkdir()
        (root / "inputs" / "01_直播回放.mp4").write_bytes(b"")
        (root / "inputs" / "02_带时间码原稿.md").write_text(SOURCE, encoding="utf-8")
        (root / "outputs" / "01_公众号可发布稿.md").write_text(DRAFT, encoding="utf-8")
        (root / "outputs" / "02_待核问题清单.md").write_text(ISSUES, encoding="utf-8")
        (root / "outputs" / "03_编辑质检报告.md").write_text(REPORT, encoding="utf-8")
        (root / "_work" / "证据映射.json").write_text(
            json.dumps(EVIDENCE, ensure_ascii=False), encoding="utf-8"
        )
        (root / "_work" / "编辑决策台账.json").write_text(
            json.dumps(EDIT_LEDGER, ensure_ascii=False), encoding="utf-8"
        )
        (root / "_work" / "说话人校准.json").write_text(
            json.dumps(SPEAKER_CALIBRATION, ensure_ascii=False), encoding="utf-8"
        )
        (root / "_work" / "说话人时间轴.json").write_text(
            json.dumps(SPEAKER_TIMELINE, ensure_ascii=False), encoding="utf-8"
        )
        (root / "_work" / "说话人冲突清单.md").write_text(
            SPEAKER_CONFLICTS, encoding="utf-8"
        )
        coverage_rows = [
            f"| 00:00–01:20 | 追问链{index} | 场景 | 主持人推进 | 嘉宾原声 | {'A' if index == 0 else 'B'} | 原样保留 | — | 第1章 |"
            for index in range(8)
        ]
        (root / "_work" / "内容覆盖表.md").write_text(
            "# 内容覆盖表\n\n"
            "| 时间段 | 原始话题/追问链 | 具体场景与细节 | 主持人推进/回应 | 嘉宾原词/语气 | 价值级别 | 处理 | 删除或合并依据 | 成稿章节 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            + "\n".join(coverage_rows)
            + "\n",
            encoding="utf-8",
        )
        (root / "_work" / "完整编辑底稿.md").write_text(DRAFT, encoding="utf-8")
        freeze_input_bundle(root)
        return root

    def make_campaign_manifest(self, case: Path, path: Path) -> Path:
        bundle = json.loads(
            (case / "_work/双轨输入清单.json").read_text(encoding="utf-8")
        )
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "campaign_name": "单元测试批次",
                    "samples": [
                        {
                            "case": case.name,
                            "source_sha256": file_sha256(
                                case / "inputs/02_带时间码原稿.md"
                            ),
                            "input_bundle_sha256": bundle["bundle_sha256"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def error_codes(self, case: Path) -> set[str]:
        return {finding.code for finding in audit_case(case) if finding.level == "ERROR"}

    def warning_codes(self, case: Path) -> set[str]:
        return {finding.code for finding in audit_case(case) if finding.level == "WARNING"}

    def test_valid_case_has_no_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            self.assertEqual(set(), self.error_codes(case))

    def test_missing_speaker_preflight_is_blocked_when_replay_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            (case / "_work" / "说话人校准.json").unlink()
            self.assertIn("MISSING_SPEAKER_PREFLIGHT", self.error_codes(case))

    def test_transcript_only_case_stops_for_missing_replay_without_fabricating_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            (case / "inputs" / "01_直播回放.mp4").unlink()
            for name in ("说话人校准.json", "说话人时间轴.json", "说话人冲突清单.md"):
                (case / "_work" / name).unlink()
            self.assertNotIn("MISSING_SPEAKER_PREFLIGHT", self.error_codes(case))
            self.assertIn("MISSING_REPLAY", self.error_codes(case))
            self.assertIn("SPEAKER_PREFLIGHT_UNAVAILABLE", self.warning_codes(case))
            release_codes = {
                item.code for item in audit_release_readiness(case, audit_case(case))
            }
            self.assertIn("RELEASE_SPEAKER_PREFLIGHT_MISSING", release_codes)
            self.assertIn("RELEASE_REPLAY_MISSING", release_codes)

    def test_unconfirmed_or_insufficient_voice_anchors_are_blocked(self) -> None:
        calibration = json.loads(json.dumps(SPEAKER_CALIBRATION, ensure_ascii=False))
        calibration["status"] = "待确认"
        calibration["speakers"][0]["reference_segments"] = calibration["speakers"][0][
            "reference_segments"
        ][:1]
        findings = audit_speaker_preflight(
            SOURCE,
            ISSUES,
            json.dumps(calibration, ensure_ascii=False),
            json.dumps(SPEAKER_TIMELINE, ensure_ascii=False),
            SPEAKER_CONFLICTS,
            Path("说话人校准.json"),
            Path("说话人时间轴.json"),
            Path("说话人冲突清单.md"),
        )
        codes = {item.code for item in findings if item.level == "ERROR"}
        self.assertIn("SPEAKER_CALIBRATION_UNCONFIRMED", codes)
        self.assertIn("SPEAKER_CALIBRATION_REFERENCES", codes)

    def test_speaker_timeline_must_cover_every_source_block_once(self) -> None:
        timeline = json.loads(json.dumps(SPEAKER_TIMELINE, ensure_ascii=False))
        timeline["source_blocks"].pop()
        findings = audit_speaker_preflight(
            SOURCE,
            ISSUES,
            json.dumps(SPEAKER_CALIBRATION, ensure_ascii=False),
            json.dumps(timeline, ensure_ascii=False),
            SPEAKER_CONFLICTS,
            Path("说话人校准.json"),
            Path("说话人时间轴.json"),
            Path("说话人冲突清单.md"),
        )
        self.assertIn(
            "SPEAKER_TIMELINE_SOURCE_COVERAGE",
            {item.code for item in findings if item.level == "ERROR"},
        )

    def test_hidden_speaker_label_conflict_cannot_be_marked_consistent(self) -> None:
        timeline = json.loads(json.dumps(SPEAKER_TIMELINE, ensure_ascii=False))
        timeline["segments"][1]["speaker_id"] = "SPK_HOST"
        timeline["source_blocks"][1]["resolved_speaker_id"] = "SPK_HOST"
        findings = audit_speaker_preflight(
            SOURCE,
            ISSUES,
            json.dumps(SPEAKER_CALIBRATION, ensure_ascii=False),
            json.dumps(timeline, ensure_ascii=False),
            SPEAKER_CONFLICTS,
            Path("说话人校准.json"),
            Path("说话人时间轴.json"),
            Path("说话人冲突清单.md"),
        )
        self.assertIn(
            "SPEAKER_TIMELINE_HIDDEN_CONFLICT",
            {item.code for item in findings if item.level == "ERROR"},
        )

    def test_mixed_speaker_block_is_tracked_as_open_p1(self) -> None:
        timeline = json.loads(json.dumps(SPEAKER_TIMELINE, ensure_ascii=False))
        timeline["segments"][1]["speaker_id"] = "SPK_HOST"
        timeline["segments"][1]["overlap"] = True
        timeline["segments"].append(
            {
                "id": "S007",
                "start": "00:10",
                "end": "00:15",
                "raw_cluster": "B",
                "speaker_id": "SPK_GUEST",
                "overlap": True,
            }
        )
        timeline["source_blocks"][1].update(
            {
                "timeline_segment_ids": ["S002", "S007"],
                "resolved_speaker_id": None,
                "status": "混合说话人",
                "conflict_id": "C001",
            }
        )
        conflicts = SPEAKER_CONFLICTS + (
            "| C001 | P1 | 00:10 | 一个原稿块含两人 | 发言人2 | "
            "SPK_HOST+SPK_GUEST | 两人抢话 | 待回听拆分 | 待确认 |\n"
        )
        issues = ISSUES.replace("- P1：0", "- P1：1") + (
            "| P1 | 00:10–00:10 | 说话人归属 | 原稿块含两人 | 回听拆分 | 待确认 |\n"
        )
        findings = audit_speaker_preflight(
            SOURCE,
            issues,
            json.dumps(SPEAKER_CALIBRATION, ensure_ascii=False),
            json.dumps(timeline, ensure_ascii=False),
            conflicts,
            Path("说话人校准.json"),
            Path("说话人时间轴.json"),
            Path("说话人冲突清单.md"),
        )
        self.assertFalse(any(item.level == "ERROR" for item in findings))
        self.assertIn(
            "SPEAKER_CONFLICT_OPEN",
            {item.code for item in findings if item.level == "WARNING"},
        )

    def test_open_speaker_conflict_must_be_listed_in_p1(self) -> None:
        timeline = json.loads(json.dumps(SPEAKER_TIMELINE, ensure_ascii=False))
        timeline["segments"][1]["speaker_id"] = "SPK_HOST"
        timeline["source_blocks"][1].update(
            {
                "resolved_speaker_id": "SPK_HOST",
                "status": "冲突待核",
                "conflict_id": "C001",
            }
        )
        conflicts = SPEAKER_CONFLICTS + (
            "| C001 | P1 | 00:10 | 标签冲突 | 发言人2 | SPK_HOST | "
            "身份不一致 | 待回听 | 待确认 |\n"
        )
        findings = audit_speaker_preflight(
            SOURCE,
            ISSUES,
            json.dumps(SPEAKER_CALIBRATION, ensure_ascii=False),
            json.dumps(timeline, ensure_ascii=False),
            conflicts,
            Path("说话人校准.json"),
            Path("说话人时间轴.json"),
            Path("说话人冲突清单.md"),
        )
        self.assertIn(
            "SPEAKER_CONFLICT_NOT_IN_ISSUES",
            {item.code for item in findings if item.level == "ERROR"},
        )

    def test_open_speaker_conflict_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            timeline_path = case / "_work" / "说话人时间轴.json"
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
            timeline["segments"][1]["speaker_id"] = "SPK_HOST"
            timeline["source_blocks"][1].update(
                {
                    "resolved_speaker_id": "SPK_HOST",
                    "status": "冲突待核",
                    "conflict_id": "C001",
                }
            )
            timeline_path.write_text(
                json.dumps(timeline, ensure_ascii=False), encoding="utf-8"
            )
            (case / "_work" / "说话人冲突清单.md").write_text(
                SPEAKER_CONFLICTS
                + "| C001 | P1 | 00:10 | 标签冲突 | 发言人2 | SPK_HOST | "
                "身份不一致 | 待回听 | 待确认 |\n",
                encoding="utf-8",
            )
            (case / "outputs" / "02_待核问题清单.md").write_text(
                ISSUES.replace("- P1：0", "- P1：1")
                + "| P1 | 00:10–00:10 | 说话人归属 | 原稿标签与时间轴冲突 | 回听确认 | 待确认 |\n",
                encoding="utf-8",
            )
            findings = audit_case(case)
            self.assertIn(
                "SPEAKER_CONFLICT_OPEN",
                {item.code for item in findings if item.level == "WARNING"},
            )
            self.assertIn(
                "RELEASE_OPEN_SPEAKER_CONFLICT",
                {item.code for item in audit_release_readiness(case, findings)},
            )

    def test_speaker_conflict_cannot_be_closed_by_context_alone(self) -> None:
        conflicts = SPEAKER_CONFLICTS + (
            "| C001 | P1 | 00:10 | 短句无法稳定归属 | 发言人2 | 无法映射 | "
            "前后都是嘉宾 | 结合上下文和问答逻辑判为嘉宾 | 已解决 |\n"
        )
        _, findings = parse_speaker_conflict_rows(conflicts, Path("说话人冲突清单.md"))
        self.assertIn(
            "SPEAKER_CONFLICT_WEAK_CLOSURE",
            {item.code for item in findings if item.level == "WARNING"},
        )

    def test_speaker_conflict_can_be_closed_with_replay_evidence(self) -> None:
        conflicts = SPEAKER_CONFLICTS + (
            "| C001 | P1 | 00:10 | 短句无法稳定归属 | 发言人2 | 无法映射 | "
            "前后都是嘉宾 | 已逐帧查看回放画面，嘉宾持续开口 | 已解决 |\n"
        )
        _, findings = parse_speaker_conflict_rows(conflicts, Path("说话人冲突清单.md"))
        self.assertNotIn(
            "SPEAKER_CONFLICT_WEAK_CLOSURE",
            {item.code for item in findings if item.level == "WARNING"},
        )

    def test_explicitly_insufficient_voiceprint_cannot_close_conflict(self) -> None:
        conflicts = SPEAKER_CONFLICTS + (
            "| C001 | P1 | 00:10 | 短句无法稳定归属 | 发言人2 | 无法映射 | "
            "低置信度窗口1个 | 短片段单独声纹不足，结合上下文判为嘉宾 | 已解决 |\n"
        )
        _, findings = parse_speaker_conflict_rows(conflicts, Path("说话人冲突清单.md"))
        self.assertIn(
            "SPEAKER_CONFLICT_WEAK_CLOSURE",
            {item.code for item in findings if item.level == "WARNING"},
        )

    def test_excessive_oral_fillers_emit_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            noisy = (
                "\n\n**示例嘉宾：** 然后我去学习，然后我去写作，然后我去运动，然后我又去旅行，"
                "然后我开始做产品，就是我觉得就是这样，然后我又继续做下去，"
                "然后我再复盘一次，然后我把这些事情全部重新做了一遍。\n"
            )
            draft_path.write_text(DRAFT + noisy, encoding="utf-8")
            self.assertIn("ORAL_FILLER_OVERLOAD", self.warning_codes(case))

    def test_post_content_shutdown_chatter_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT
                + "\n\n**示例主持：** 谁帮我们截个图？\n\n"
                + "**嘉宾：** 我现在好白。\n\n"
                + "**示例主持：** 小宝贝，看这里。\n\n"
                + "**嘉宾：** 我涂了防晒，应该是结束了。\n",
                encoding="utf-8",
            )
            self.assertIn("POST_CONTENT_SHUTDOWN_CHATTER", self.error_codes(case))

    def test_intro_internal_process_jargon_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT.replace(
                    "最近，我和示例嘉宾在直播间做了一场关于重启人生的对谈。",
                    "这是根据直播原稿轻度校订的文字版。最近，我和示例嘉宾在直播间做了一场关于重启人生的对谈。",
                ),
                encoding="utf-8",
            )
            self.assertIn("INTRO_INTERNAL_PROCESS_JARGON", self.error_codes(case))

    def test_low_value_ending_runway_emits_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT
                + "\n\n**主持人：** 怎么时间过得这么快。\n\n"
                + "**嘉宾：** 我也舍不得，咖啡喝得太浓，喝了就睡不着。\n\n"
                + "**主持人：** 直播间有没有单身女性看上这位帅小伙？\n\n"
                + "**嘉宾：** 最后我想说，重要的是重新开始。\n",
                encoding="utf-8",
            )
            self.assertIn("ENDING_RUNWAY_DRIFT", self.warning_codes(case))

    def test_long_draft_without_reading_landmarks_emits_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT
                + "\n\n## 03<br>补充长章节\n\n**主持人：** "
                + ("这一段仍然来自直播原话并用于检验长文视觉导航。" * 500),
                encoding="utf-8",
            )
            warnings = self.warning_codes(case)
            self.assertIn("SPARSE_READING_LANDMARKS", warnings)
            self.assertIn("SECTION_SIZE_IMBALANCE", warnings)

    def test_reader_facing_operational_placeholder_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT + "\n\n[运营素材待补：账号卡片和二维码]\n",
                encoding="utf-8",
            )
            self.assertIn("UNFINISHED_PLACEHOLDER", self.error_codes(case))

    def test_oversized_dialogue_paragraph_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT + "\n\n## 03<br>补充章节\n\n**示例嘉宾：** " + ("这段话需要自然分段" * 70),
                encoding="utf-8",
            )
            self.assertIn("OVERSIZED_DIALOGUE_PARAGRAPH", self.error_codes(case))

    def test_local_timeline_gap_is_blocked_even_when_other_sections_are_long(self) -> None:
        phrases = ["苹果山村真实经历", "海洋城市工作变化", "星空课堂学习方法"]
        source_parts = []
        for bucket, phrase in enumerate(phrases):
            for minute_offset in range(15):
                minute = bucket * 15 + minute_offset
                speaker = 1 if minute_offset % 2 == 0 else 2
                source_parts.append(
                    f"发言人{speaker}   {minute:02d}:00\n{phrase * 35}\n"
                )
        source = "\n".join(source_parts)
        draft = (
            "**嘉宾：** " + phrases[0] * 80 + "\n\n"
            "**主持人：** " + phrases[0] * 40 + "\n\n"
            "**嘉宾：** " + phrases[2] * 80 + "\n\n"
            "**主持人：** " + phrases[2] * 40 + "\n"
        )
        ledger = json.dumps(
            {"speaker_map": {"发言人1": "嘉宾", "发言人2": "主持人"}},
            ensure_ascii=False,
        )
        profile = timeline_retention_profile(
            source, draft, {"发言人1": "嘉宾", "发言人2": "主持人"}
        )
        self.assertEqual(3, len(profile))
        findings = audit_timeline_retention(source, draft, ledger, Path("draft.md"))
        self.assertIn(
            "LOCAL_TIMELINE_OVERCOMPRESSED", {item.code for item in findings}
        )

    def test_declared_shutdown_tail_is_excluded_from_local_retention(self) -> None:
        source = "\n".join(
            [
                f"发言人{1 if minute % 2 == 0 else 2}   {minute:02d}:00\n"
                + ("有价值的完整问答" * 45)
                for minute in range(30)
            ]
            + [
                f"发言人{1 if minute % 2 == 0 else 2}   {minute:02d}:00\n"
                + ("截图看镜头准备关播" * 45)
                for minute in range(30, 45)
            ]
        )
        draft = "\n\n".join(
            f"**{'嘉宾' if minute % 2 == 0 else '主持人'}：** " + ("有价值的完整问答" * 45)
            for minute in range(30)
        )
        ledger = json.dumps(
            {"speaker_map": {"发言人1": "嘉宾", "发言人2": "主持人"}},
            ensure_ascii=False,
        )
        coverage = "- 发布稿语义结束点：30:00（其后仅为截图和关播）"
        findings = audit_timeline_retention(
            source, draft, ledger, Path("draft.md"), coverage=coverage
        )
        self.assertNotIn(
            "LOCAL_TIMELINE_OVERCOMPRESSED", {item.code for item in findings}
        )

    def test_declared_shutdown_tail_is_excluded_from_global_retention(self) -> None:
        source = (
            "发言人1   00:00\n" + ("有效问答" * 2600) + "\n"
            "发言人2   30:00\n" + ("截图关播闲聊" * 2600)
        )
        draft = "**嘉宾：** " + ("有效问答" * 1400)
        without_cutoff = audit_retention(source, draft, Path("draft.md"))
        with_cutoff = audit_retention(
            source,
            draft,
            Path("draft.md"),
            coverage="- 发布稿语义结束点：30:00",
        )
        self.assertIn(
            "OVER_COMPRESSED_IMMERSIVE_DRAFT", {item.code for item in without_cutoff}
        )
        self.assertNotIn(
            "OVER_COMPRESSED_IMMERSIVE_DRAFT", {item.code for item in with_cutoff}
        )

    def test_semantic_end_cannot_exclude_more_than_five_minutes(self) -> None:
        source = "\n".join(
            f"发言人{1 if minute % 2 == 0 else 2}   {minute:02d}:00\n有效问答"
            for minute in range(0, 41, 5)
        )
        rows = []
        for start in range(0, 40, 5):
            level = "C" if start >= 30 else "A"
            treatment = "发布稿删除" if level == "C" else "完整保留"
            rows.append(
                f"| {start:02d}:00–{start + 5:02d}:00 | 话题 | 场景 | 追问 | 原词 | {level} | {treatment} | 依据 | 章节 |"
            )
        coverage = (
            "- 发布稿语义结束点：30:00\n\n"
            "| 时间段 | 原始话题 | 场景 | 主持人 | 嘉宾 | 价值级别 | 处理 | 依据 | 章节 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            + "\n".join(rows)
        )
        findings = audit_coverage_map(coverage, source, Path("coverage.md"))
        self.assertIn("SEMANTIC_END_TOO_EARLY", {item.code for item in findings})

    def test_speaker_retention_imbalance_is_blocked(self) -> None:
        guest = "嘉宾讲述具体经历和判断"
        host = "主持人持续追问并展开判断"
        source = "\n".join(
            f"发言人{1 if index % 2 == 0 else 2}   {index:02d}:00\n"
            + ((guest if index % 2 == 0 else host) * 45)
            for index in range(30)
        )
        draft = "**嘉宾：** " + guest * 400
        ledger = json.dumps(
            {"speaker_map": {"发言人1": "嘉宾", "发言人2": "主持人"}},
            ensure_ascii=False,
        )
        findings = audit_speaker_retention_balance(
            source, draft, ledger, Path("draft.md")
        )
        self.assertIn(
            "SPEAKER_RETENTION_IMBALANCE", {item.code for item in findings}
        )

    def test_both_speakers_can_be_balanced_but_still_too_thin(self) -> None:
        guest = "嘉宾讲述具体经历和判断"
        host = "主持人持续追问并展开判断"
        source = "\n".join(
            f"发言人{1 if index % 2 == 0 else 2}   {index:02d}:00\n"
            + ((guest if index % 2 == 0 else host) * 50)
            for index in range(20)
        )
        draft = (
            "**嘉宾：** " + guest * 225 + "\n\n"
            "**主持人：** " + host * 225 + "\n"
        )
        ledger = json.dumps(
            {"speaker_map": {"发言人1": "嘉宾", "发言人2": "主持人"}},
            ensure_ascii=False,
        )
        findings = audit_speaker_retention_balance(source, draft, ledger, Path("draft.md"))
        self.assertIn("SPEAKER_RETENTION_LOW", {item.code for item in findings})
        self.assertNotIn("SPEAKER_RETENTION_IMBALANCE", {item.code for item in findings})

    def test_low_confidence_cross_speaker_paraphrase_emits_warning(self) -> None:
        source = """发言人1   00:00
他第一次肯定改不到位，因为不可能听一遍课程就理解全部内容。第二稿第三稿继续改，才能不断接近标准。

发言人2   00:30
你认为反复修改是正常的学习过程，对吗？
"""
        draft = """**主持人：** 他不可能听一遍课程，就把所有技巧一下理解。第一稿写错了，第二稿第三稿继续改，他就不断接近标准。
"""
        ledger = json.dumps(
            {"speaker_map": {"发言人1": "嘉宾", "发言人2": "主持人"}},
            ensure_ascii=False,
        )
        findings = audit_speaker_attribution(source, draft, ledger, Path("draft.md"))
        self.assertIn(
            "LOW_CONFIDENCE_SPEAKER_ATTRIBUTION",
            {item.code for item in findings},
        )

    def test_source_order_reversal_is_blocked(self) -> None:
        source = """发言人1   00:00
先讲失败经历和别人家的孩子，这段经历让我能够接受失败。

发言人2   00:30
失败过很多次，也未必是一件坏事。

发言人1   03:00
后来再聊别人嫉妒我，我会把自己放在很低的位置。

发言人2   03:30
你会怎么减少别人对你的敌意？
"""
        draft = """**嘉宾：** 后来再聊别人嫉妒我，我会把自己放在很低的位置。

**主持人：** 你会怎么减少别人对你的敌意？

**嘉宾：** 先讲失败经历和别人家的孩子，这段经历让我能够接受失败。

**主持人：** 失败过很多次，也未必是一件坏事。
"""
        ledger = json.dumps(
            {"speaker_map": {"发言人1": "嘉宾", "发言人2": "主持人"}, "nontrivial_edits": []},
            ensure_ascii=False,
        )
        findings = audit_source_order(source, draft, ledger, Path("draft.md"))
        self.assertIn("DRAFT_SOURCE_ORDER_REVERSAL", {item.code for item in findings})

    def test_title_number_must_appear_in_verification_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(DRAFT.replace("# 从困境里重新开始", "# 16岁从困境里重新开始"), encoding="utf-8")
            self.assertIn("TITLE_NUMBER_NOT_IN_ISSUES", self.error_codes(case))

    def test_evidence_anchor_must_belong_to_declared_heading(self) -> None:
        source = SOURCE + ("补充访谈内容" * 2000)
        evidence = {
            "version": 1,
            "source": "inputs/02_带时间码原稿.md",
            "sections": [
                {
                    "module_id": f"M{index:02d}",
                    "draft_heading": "01<br>过去的十年<br>她如何重新找回自己",
                    "draft_anchor": "学习计划被打断不可怕" if index == 1 else "过去十年我一直在照顾家庭",
                    "source_ranges": [{"start": "00:10", "end": "00:40"}],
                    "key_details": ["细节"],
                    "verification_status": "已核对原稿",
                    "edit_decision_ids": [],
                }
                for index in range(1, 9)
            ],
        }
        findings = audit_evidence(
            json.dumps(evidence, ensure_ascii=False),
            source,
            DRAFT,
            Path("evidence.json"),
            edit_ledger=json.dumps(EDIT_LEDGER, ensure_ascii=False),
        )
        self.assertIn(
            "EVIDENCE_ANCHOR_HEADING_DRIFT", {item.code for item in findings}
        )

    def test_report_claim_must_match_current_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            report_path = case / "outputs" / "03_编辑质检报告.md"
            report_path.write_text(
                REPORT + "\n- ASR处理：暂按“于是我干脆不读了”保守推定。\n",
                encoding="utf-8",
            )
            self.assertIn("REPORT_DRAFT_SEMANTIC_DRIFT", self.error_codes(case))

    def test_missing_efficiency_record_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            report_path = case / "outputs" / "03_编辑质检报告.md"
            report_path.write_text(
                re.sub(r"^- (?:AI 整理耗时|人工终审耗时|总耗时|直接费用|相比原工作流程节省时间)：.*\n", "", REPORT, flags=re.MULTILINE),
                encoding="utf-8",
            )
            self.assertIn("MISSING_EFFICIENCY_RECORD", self.error_codes(case))

    def test_efficiency_record_accepts_ai_label_without_space(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            report_path = case / "outputs" / "03_编辑质检报告.md"
            report_path.write_text(
                REPORT.replace("AI 整理耗时", "AI整理耗时"), encoding="utf-8"
            )
            self.assertNotIn("MISSING_EFFICIENCY_RECORD", self.error_codes(case))

    def test_machine_transcript_hashes_detect_stale_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clip_dir = Path(directory)
            manifest = clip_dir / "manifest.json"
            manifest.write_text('{"clips":[],"samples":[]}', encoding="utf-8")
            audio = clip_dir / "clip.m4a"
            audio.write_bytes(b"audio-v1")
            transcript = {
                "manifest_sha256": file_sha256(manifest),
                "clips": [
                    {
                        "filename": audio.name,
                        "audio_sha256": file_sha256(audio),
                        "text": "机器转写",
                        "error": None,
                    }
                ],
            }
            (clip_dir / "machine_transcripts.json").write_text(
                json.dumps(transcript, ensure_ascii=False), encoding="utf-8"
            )
            self.assertEqual(
                [], audit_machine_transcripts(clip_dir, manifest, {audio.name})
            )
            audio.write_bytes(b"audio-v2")
            codes = {
                item.code
                for item in audit_machine_transcripts(
                    clip_dir, manifest, {audio.name}
                )
            }
            self.assertIn("MACHINE_TRANSCRIPT_AUDIO_DRIFT", codes)

    def test_near_duplicate_same_speaker_paragraphs_emit_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            repeated = (
                "\n\n**示例嘉宾：** 我听一位老师说，贵的人只会在贵的地方出现。"
                "所以我开始去付费圈子认识人，因为那里的人已经做过一次选择，也更有行动力，"
                "我确实在那里认识了很多有水平的朋友。\n\n"
                "我以前也听老师说过，贵的人只会在贵的地方出现。后来我就去付费圈子认识人，"
                "因为那些人已经做过选择，行动速度通常也更快，我也在那里认识了很多有水平的朋友。\n"
            )
            draft_path.write_text(DRAFT + repeated, encoding="utf-8")
            self.assertIn("NEAR_DUPLICATE_PARAGRAPH", self.warning_codes(case))
            findings = audit_case(case)
            release_codes = {
                item.code for item in audit_release_readiness(case, findings)
            }
            self.assertIn("RELEASE_NEAR_DUPLICATE_UNREVIEWED", release_codes)

            report_path = case / "outputs" / "03_编辑质检报告.md"
            report_path.write_text(
                REPORT + "\n- 近义重复人工复核：已完成\n", encoding="utf-8"
            )
            release_codes = {
                item.code
                for item in audit_release_readiness(case, audit_case(case))
            }
            self.assertNotIn("RELEASE_NEAR_DUPLICATE_UNREVIEWED", release_codes)

    def test_negative_certainty_statement_is_not_a_result_promise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT.replace(
                    "学习计划被打断不可怕",
                    "这并不是百分之百确定会成功，学习计划被打断不可怕",
                ),
                encoding="utf-8",
            )
            self.assertNotIn("HIGH_RISK_CLAIM", self.warning_codes(case))

    def test_positive_result_promise_emits_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT.replace(
                    "学习计划被打断不可怕",
                    "参加以后百分之百能成功，学习计划被打断不可怕",
                ),
                encoding="utf-8",
            )
            self.assertIn("HIGH_RISK_CLAIM", self.warning_codes(case))

    def test_repeated_same_speaker_label_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT
                + "\n\n**示例嘉宾：** 这是同一个人的第一段。\n\n"
                + "**示例嘉宾：** 这是同一个人的第二段，中间没有他人插话或章节切换。\n",
                encoding="utf-8",
            )
            self.assertIn("REDUNDANT_SPEAKER_LABEL", self.error_codes(case))

    def test_repeated_same_speaker_after_heading_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT
                + "\n\n## 03<br>新的章节\n\n**示例嘉宾：** 这是新章节开始后的发言。\n",
                encoding="utf-8",
            )
            self.assertNotIn("REDUNDANT_SPEAKER_LABEL", self.error_codes(case))

    def test_suspicious_first_person_speaker_sandwich_emits_warning(self) -> None:
        source = """发言人1   02:00
我今天状态很好，咖啡喝多了可能睡不着。

发言人2   02:05
我直播前喝了一杯双倍浓缩。

发言人1   02:10
然后又喝了一杯，我平时喝了就睡不着。
"""
        findings = audit_suspicious_speaker_sandwiches(
            source,
            json.dumps(EDIT_LEDGER, ensure_ascii=False),
            Path("source.md"),
        )
        self.assertIn("SUSPICIOUS_SPEAKER_SANDWICH", {item.code for item in findings})

    def test_confirmed_speaker_sandwich_correction_clears_warning(self) -> None:
        source = """发言人1   02:00
我今天状态很好，咖啡喝多了可能睡不着。

发言人2   02:05
我直播前喝了一杯双倍浓缩。

发言人1   02:10
然后又喝了一杯，我平时喝了就睡不着。
"""
        ledger = dict(EDIT_LEDGER)
        ledger["nontrivial_edits"] = [
            {
                "type": "说话人纠正",
                "start": "02:05",
                "end": "02:05",
                "status": "已解决",
            }
        ]
        findings = audit_suspicious_speaker_sandwiches(
            source,
            json.dumps(ledger, ensure_ascii=False),
            Path("source.md"),
        )
        self.assertNotIn("SUSPICIOUS_SPEAKER_SANDWICH", {item.code for item in findings})

    def test_high_confidence_speaker_attribution_conflict_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT.replace(
                    "**示例嘉宾：** 我是示例嘉宾，过去十年我一直在照顾家庭。",
                    "**主持人：** 我是示例嘉宾，过去十年我一直在照顾家庭。",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertIn("SPEAKER_ATTRIBUTION_MISMATCH", self.error_codes(case))

    def test_tracked_speaker_correction_is_not_reported_as_untracked_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT.replace(
                    "**示例嘉宾：** 我是示例嘉宾，过去十年我一直在照顾家庭。",
                    "**主持人：** 我是示例嘉宾，过去十年我一直在照顾家庭。",
                    1,
                ),
                encoding="utf-8",
            )
            ledger = json.loads(json.dumps(EDIT_LEDGER, ensure_ascii=False))
            ledger["nontrivial_edits"] = [
                {
                    "id": "E001",
                    "start": "00:10",
                    "end": "00:10",
                    "type": "说话人纠正",
                    "source_excerpt": "我是示例嘉宾，过去十年我一直在照顾家庭",
                    "draft_excerpt": "我是示例嘉宾，过去十年我一直在照顾家庭",
                    "basis": "原稿上下文",
                    "confidence": "暂定",
                    "status": "待确认",
                    "notes": "等待回听",
                }
            ]
            (case / "_work" / "编辑决策台账.json").write_text(
                json.dumps(ledger, ensure_ascii=False), encoding="utf-8"
            )
            issue_path = case / "outputs" / "02_待核问题清单.md"
            issue_path.write_text(
                ISSUES.replace("- P1：0", "- P1：1项待确认")
                + "| P1 | 00:10 | 说话人边界 | 原文 | 回听 | 待确认 |\n",
                encoding="utf-8",
            )
            self.assertNotIn("SPEAKER_ATTRIBUTION_MISMATCH", self.error_codes(case))

    def test_source_labeled_speaker_boundary_restore_does_not_require_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            ledger = json.loads(json.dumps(EDIT_LEDGER, ensure_ascii=False))
            ledger["nontrivial_edits"] = [
                {
                    "id": "E001",
                    "start": "00:10",
                    "end": "00:10",
                    "type": "说话人边界还原",
                    "source_excerpt": "我是示例嘉宾",
                    "draft_excerpt": "我是示例嘉宾",
                    "basis": "带时间码原稿",
                    "confidence": "已确认",
                    "status": "已解决",
                    "notes": "恢复原稿已经明确标注的说话人切换",
                }
            ]
            (case / "_work" / "编辑决策台账.json").write_text(
                json.dumps(ledger, ensure_ascii=False), encoding="utf-8"
            )
            codes = self.error_codes(case)
            self.assertNotIn("EDIT_LEDGER_TYPE", codes)
            self.assertNotIn("EDIT_LEDGER_BASIS", codes)
            self.assertNotIn("SPEAKER_EDIT_WITHOUT_REPLAY", codes)

    def test_obvious_asr_residue_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(DRAFT + "\n\n**示例嘉宾：** 我我当时因因为紧张，就就没有继续说。\n", encoding="utf-8")
            self.assertIn("ASR_RESIDUE", self.error_codes(case))

    def test_heavy_rewrite_density_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            source_path = case / "inputs" / "02_带时间码原稿.md"
            source_path.write_text(
                SOURCE + "\n" + ("原稿讲述跑步、河流、村庄和童年的具体过程。" * 300),
                encoding="utf-8",
            )
            paragraphs = "\n\n".join(
                f"**{'主持人' if index % 2 == 0 else '示例嘉宾'}：** 量子芯片改变星际航行方式，抽象模型重新定义未来组织形态，并讨论完全不存在于来源材料的火星城市治理方案第{index}次。"
                for index in range(20)
            )
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(DRAFT + "\n\n## 03<br>新增章节\n\n" + paragraphs, encoding="utf-8")
            self.assertIn("HEAVY_REWRITE_DENSITY", self.error_codes(case))

    def test_provisional_edit_must_be_listed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            ledger = dict(EDIT_LEDGER)
            ledger["nontrivial_edits"] = [
                {
                    "id": "E001",
                    "start": "00:10",
                    "end": "00:10",
                    "type": "ASR语义修复",
                    "source_excerpt": "我是示例嘉宾",
                    "draft_excerpt": "我是示例嘉宾",
                    "basis": "原稿上下文",
                    "confidence": "暂定",
                    "status": "待确认",
                    "notes": "等待回听",
                }
            ]
            (case / "_work" / "编辑决策台账.json").write_text(
                json.dumps(ledger, ensure_ascii=False), encoding="utf-8"
            )
            self.assertIn("PROVISIONAL_EDIT_NOT_LISTED", self.error_codes(case))

    def test_speaker_reassignment_requires_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            ledger = dict(EDIT_LEDGER)
            ledger["nontrivial_edits"] = [
                {
                    "id": "E001",
                    "start": "00:10",
                    "end": "00:10",
                    "type": "说话人纠正",
                    "source_excerpt": "我是示例嘉宾",
                    "draft_excerpt": "我是示例嘉宾",
                    "basis": "原稿上下文",
                    "confidence": "已确认",
                    "status": "已解决",
                    "notes": "没有回放证据",
                }
            ]
            (case / "_work" / "编辑决策台账.json").write_text(
                json.dumps(ledger, ensure_ascii=False), encoding="utf-8"
            )
            self.assertIn("SPEAKER_EDIT_WITHOUT_REPLAY", self.error_codes(case))

    def test_editor_note_requires_ledger_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(
                DRAFT + "\n\n> [编辑说明：这里删除了一段涉及第三方的敏感经历。]\n",
                encoding="utf-8",
            )
            self.assertIn("UNTRACKED_EDITOR_NOTE", self.error_codes(case))

    def test_issue_summary_must_match_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            issue_path = case / "outputs" / "02_待核问题清单.md"
            issue_path.write_text(
                ISSUES.replace("- P1：0", "- P1：2项待确认")
                + "| P1 | 00:10 | ASR语义 | 原文 | 回听 | 待确认 |\n",
                encoding="utf-8",
            )
            self.assertIn("ISSUE_SUMMARY_MISMATCH", self.error_codes(case))

    def test_pending_issue_requires_precise_timecode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            issue_path = case / "outputs" / "02_待核问题清单.md"
            issue_path.write_text(
                ISSUES.replace("- P1：0", "- P1：1项待确认")
                + "| P1 | 全程 | 说话人 | 原文 | 回听 | 待确认 |\n",
                encoding="utf-8",
            )
            self.assertIn("ISSUE_NO_PRECISE_TIMECODE", self.error_codes(case))

    def test_pending_issue_range_over_ninety_seconds_is_too_broad(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            issue_path = case / "outputs" / "02_待核问题清单.md"
            issue_path.write_text(
                ISSUES.replace("- P1：0", "- P1：1项待确认")
                + "| P1 | 00:10–02:00 | 数字 | 原文 | 回听 | 待确认 |\n",
                encoding="utf-8",
            )
            self.assertIn("ISSUE_TIMECODE_TOO_BROAD", self.error_codes(case))

    def test_pending_issue_timecode_must_be_in_source_range_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            issue_path = case / "outputs" / "02_待核问题清单.md"
            issue_path.write_text(
                ISSUES.replace("- P1：0", "- P1：2项待确认")
                + "| P1 | 09:00 | 数字 | 原文 | 回听 | 待确认 |\n"
                + "| P1 | 01:00–00:40 | 说话人 | 原文 | 回听 | 待确认 |\n",
                encoding="utf-8",
            )
            codes = self.error_codes(case)
            self.assertIn("ISSUE_TIMECODE_OUT_OF_RANGE", codes)
            self.assertIn("ISSUE_TIMECODE_ORDER", codes)

    def test_many_pending_issues_require_matching_review_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            issue_path = case / "outputs" / "02_待核问题清单.md"
            rows = "".join(
                f"| P1 | 00:{10 + index:02d} | 数字{index} | 原文 | 回听 | 待确认 |\n"
                for index in range(5)
            )
            issue_path.write_text(
                ISSUES.replace("- P1：0", "- P1：5项待确认") + rows,
                encoding="utf-8",
            )
            self.assertIn("MISSING_REVIEW_SHEET", self.error_codes(case))
            issue_filenames = [f"P1-{index:02d}-01-00h00m00s.m4a" for index in range(1, 6)]
            sample_filenames = [f"SAMPLE-{index:02d}-00h00m00s.m4a" for index in range(1, 6)]
            review_dir = case / "_work" / "review_clips"
            review_dir.mkdir()
            for filename in issue_filenames + sample_filenames:
                (review_dir / filename).write_bytes(b"audio")
            (review_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "clips": [
                            {"issue_index": index, "filename": filename}
                            for index, filename in enumerate(issue_filenames, start=1)
                        ],
                        "samples": [
                            {"issue_index": index, "filename": filename}
                            for index, filename in enumerate(sample_filenames, start=1)
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (case / "_work" / "回听终审表.md").write_text(
                "# 回听终审表\n\n"
                + "".join(
                    f"| {index} | `{issue_filenames[index - 1]}` | 待填 |\n"
                    for index in range(1, 6)
                )
                + "".join(
                    f"| 00:{30 + index:02d} | `{sample_filenames[index - 1]}` | 待填 |\n"
                    for index in range(1, 6)
                ),
                encoding="utf-8",
            )
            index_result = build_review_index(case)
            self.assertEqual(10, index_result["files"])
            codes = self.error_codes(case)
            self.assertNotIn("MISSING_REVIEW_SHEET", codes)
            self.assertNotIn("REVIEW_SHEET_DRIFT", codes)
            self.assertNotIn("REVIEW_CLIP_SHEET_DRIFT", codes)

            (review_dir / issue_filenames[0]).unlink()
            self.assertIn("REVIEW_CLIP_FILE_MISSING", self.error_codes(case))

    def test_long_interview_requires_random_review_even_without_open_p1(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            source_path = case / "inputs" / "02_带时间码原稿.md"
            source_path.write_text(
                SOURCE + "\n" + ("这是用于模拟长访谈的具体对话内容。" * 800),
                encoding="utf-8",
            )
            self.assertIn("MISSING_REVIEW_SHEET", self.error_codes(case))

    def test_clip_cleanup_only_removes_manifest_managed_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            stale = output_dir / "old.m4a"
            keep = output_dir / "keep.m4a"
            user_file = output_dir / "manual-note.m4a"
            for path in (stale, keep, user_file):
                path.write_bytes(b"audio")
            (output_dir / "manifest.json").write_text(
                json.dumps(
                    {"clips": [{"filename": "old.m4a"}, {"filename": "keep.m4a"}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                {"old.m4a", "keep.m4a"},
                managed_clip_filenames(output_dir / "manifest.json"),
            )
            self.assertEqual(1, remove_stale_managed_clips(output_dir, {"keep.m4a"}))
            self.assertFalse(stale.exists())
            self.assertTrue(keep.exists())
            self.assertTrue(user_file.exists())

    def test_incomplete_staging_keeps_existing_review_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = root / "review_clips"
            staging.mkdir()
            output.mkdir()
            old_clip = output / "old.m4a"
            old_clip.write_bytes(b"old-audio")
            old_manifest = '{"clips":[{"filename":"old.m4a"}],"samples":[]}'
            (output / "manifest.json").write_text(old_manifest, encoding="utf-8")
            planned = [
                ReviewClip("P1", 1, "00:10", "数字", "核对", 2, 30, "new-1.m4a"),
                ReviewClip("P1", 2, "00:40", "数字", "核对", 32, 30, "new-2.m4a"),
            ]
            (staging / "new-1.m4a").write_bytes(b"new-audio")
            with self.assertRaises(RuntimeError):
                install_staged_package(
                    staging,
                    output,
                    planned,
                    {"clips": [], "samples": []},
                    review_sheet_path=root / "回听终审表.md",
                    review_sheet_text="# 新表\n",
                )
            self.assertEqual(b"old-audio", old_clip.read_bytes())
            self.assertEqual(old_manifest, (output / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse((root / "回听终审表.md").exists())

    def test_staged_install_accepts_shared_audio_for_multiple_issues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            output = root / "review_clips"
            staging.mkdir()
            shared = "P1-01-01-00h00m02s.m4a"
            (staging / shared).write_bytes(b"shared-audio")
            planned = [
                ReviewClip("P1", 1, "00:10", "身份", "核对", 2, 30, shared),
                ReviewClip("P1", 2, "00:10", "业绩", "核对", 2, 30, shared),
            ]
            install_staged_package(
                staging,
                output,
                planned,
                {"clips": [], "samples": []},
                review_sheet_path=root / "回听终审表.md",
                review_sheet_text=None,
            )
            self.assertEqual(b"shared-audio", (output / shared).read_bytes())

    def test_review_manifest_allows_only_exact_issue_window_reuse(self) -> None:
        shared = "shared.m4a"
        clips = [
            {"issue_index": 1, "filename": shared, "start": 10, "duration": 40},
            {"issue_index": 2, "filename": shared, "start": 10, "duration": 40},
        ]
        self.assertEqual(set(), conflicting_review_filenames(clips, []))
        clips[1]["start"] = 20
        self.assertEqual({shared}, conflicting_review_filenames(clips, []))
        self.assertEqual(
            {"sample.m4a"},
            conflicting_review_filenames(
                [],
                [{"filename": "sample.m4a"}, {"filename": "sample.m4a"}],
            ),
        )
        self.assertEqual(
            {shared},
            conflicting_review_filenames(
                [{"filename": shared, "start": 10, "duration": 40}],
                [{"filename": shared}],
            ),
        )

    def test_overlapping_review_windows_are_merged_without_exceeding_limit(self) -> None:
        self.assertEqual(
            [(100, 76), (220, 46)],
            merge_overlapping_windows([(100, 46), (130, 46), (220, 46)], max_duration=90),
        )
        self.assertEqual(
            [(100, 60), (150, 60)],
            merge_overlapping_windows([(100, 60), (150, 60)], max_duration=90),
        )

    def test_stratified_samples_are_stable_and_avoid_issue_windows(self) -> None:
        source = "\n".join(
            f"发言人1   {minute:02d}:00\n第{minute}分钟的内容"
            for minute in range(0, 60)
        )
        excluded = [
            type("Clip", (), {"start": 600, "duration": 60})(),
            type("Clip", (), {"start": 1800, "duration": 60})(),
        ]
        first = select_stratified_samples(source, excluded)
        second = select_stratified_samples(source, excluded)
        self.assertEqual(first, second)
        self.assertEqual(5, len(first))
        self.assertTrue(all(sample.priority == "SAMPLE" for sample in first))
        self.assertTrue(
            all(not (540 <= sample.start + 8 <= 720) for sample in first)
        )

    def test_stratified_samples_ignore_header_clock_times(self) -> None:
        source = "录制导出时间：23:37:00\n\n" + "\n".join(
            f"发言人1   {minute:02d}:00\n第{minute}分钟的内容"
            for minute in range(0, 60)
        )
        samples = select_stratified_samples(source, [])
        self.assertEqual(5, len(samples))
        self.assertTrue(all(sample.start < 60 * 60 for sample in samples))

    def test_dual_track_package_is_anonymous_and_counterbalanced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_root = root / "case"
            case_root.mkdir()
            case = self.make_case(case_root)
            comparison = root / "人工稿.md"
            comparison.write_text(
                DRAFT.replace("每天抽出一小时学习", "每天留出固定时间学习"),
                encoding="utf-8",
            )
            output = root / "review"
            campaign = self.make_campaign_manifest(case, root / "campaign.json")
            manifest = prepare_review_package(
                case,
                case / "outputs" / "01_公众号可发布稿.md",
                comparison,
                output,
                readers=4,
                campaign_manifest=campaign,
            )
            self.assertEqual(4, len(manifest["reader_orders"]))
            self.assertEqual(["A", "B"], manifest["reader_orders"][0]["order"])
            self.assertEqual(["B", "A"], manifest["reader_orders"][1]["order"])
            self.assertTrue((output / "R01/01_版本A.md").is_file())
            self.assertTrue((output / "R02/01_版本B.md").is_file())
            self.assertTrue((output / "双轨质量对比表.md").is_file())
            self.assertNotIn(
                "private_mapping",
                (output / "R01/03_匿名评审表.md").read_text(encoding="utf-8"),
            )

    def test_dual_track_package_rejects_identical_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_root = root / "case"
            case_root.mkdir()
            case = self.make_case(case_root)
            draft = case / "outputs" / "01_公众号可发布稿.md"
            campaign = self.make_campaign_manifest(case, root / "campaign.json")
            with self.assertRaises(ValueError):
                prepare_review_package(
                    case,
                    draft,
                    draft,
                    root / "review",
                    readers=3,
                    campaign_manifest=campaign,
                )

    def test_true_dual_track_requires_preregistered_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_root = root / "case"
            case_root.mkdir()
            case = self.make_case(case_root)
            comparison = root / "人工稿.md"
            comparison.write_text(
                DRAFT.replace("每天抽出一小时学习", "每天留出固定时间学习"),
                encoding="utf-8",
            )
            with self.assertRaises(FileNotFoundError):
                prepare_review_package(
                    case,
                    case / "outputs/01_公众号可发布稿.md",
                    comparison,
                    root / "review",
                    readers=3,
                )

    def test_frozen_input_bundle_rejects_later_source_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            source_path = case / "inputs/02_带时间码原稿.md"
            source_path.write_text(
                source_path.read_text(encoding="utf-8") + "\n后加内容\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                verify_input_bundle(case)

    def test_dual_track_package_rejects_exposed_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_root = root / "case"
            case_root.mkdir()
            case = self.make_case(case_root)
            comparison = root / "人工稿.md"
            comparison.write_text(
                DRAFT.replace("每天抽出一小时学习", "每天留出固定时间学习"),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                prepare_review_package(
                    case,
                    case / "outputs" / "01_公众号可发布稿.md",
                    comparison,
                    root / "review",
                    readers=3,
                    experiment_type="双轨对比",
                    reference_exposure="生成阶段已见对照稿",
                )

    def test_human_review_baseline_and_metrics_are_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            manifest = freeze_baseline(case)
            self.assertEqual(
                manifest,
                freeze_baseline(case),
            )
            draft_path = case / "outputs/01_公众号可发布稿.md"
            draft_path.write_text(
                draft_path.read_text(encoding="utf-8").replace(
                    "一点点做出改变", "一步一步做出改变"
                ),
                encoding="utf-8",
            )
            result = summarize_human_review(
                case,
                "编辑甲",
                "2026-07-21",
                28,
            )
            self.assertGreater(result["human_modification_rate"], 0)
            self.assertGreater(result["changed_paragraph_units"], 0)
            self.assertTrue((case / "_work/人工终审修改量.json").is_file())
            self.assertTrue((case / "_work/人工终审修改量.md").is_file())

    def test_human_review_baseline_refuses_silent_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            freeze_baseline(case)
            draft_path = case / "outputs/01_公众号可发布稿.md"
            draft_path.write_text(
                draft_path.read_text(encoding="utf-8") + "\n人工修改。\n",
                encoding="utf-8",
            )
            with self.assertRaises(FileExistsError):
                freeze_baseline(case)

    def test_dual_track_summary_requires_source_and_three_valid_readers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_root = root / "case"
            case_root.mkdir()
            case = self.make_case(case_root)
            comparison = root / "人工稿.md"
            comparison.write_text(
                DRAFT.replace("每天抽出一小时学习", "每天留出固定时间学习"),
                encoding="utf-8",
            )
            output = root / "review"
            campaign = self.make_campaign_manifest(case, root / "campaign.json")
            manifest = prepare_review_package(
                case,
                case / "outputs" / "01_公众号可发布稿.md",
                comparison,
                output,
                readers=4,
                campaign_manifest=campaign,
            )
            choices = {"R01": "A", "R02": "B", "R03": "A"}
            for reader, choice in choices.items():
                form = output / reader / "03_匿名评审表.md"
                text = form.read_text(encoding="utf-8").replace(
                    "- 评审状态：待填写", "- 评审状态：已完成"
                )
                for criterion in (
                    "更像在听直播",
                    "更愿意读完",
                    "人物更鲜活",
                    "长但不拖沓",
                    "更适合直接发布",
                    "最终偏好",
                ):
                    text = re.sub(
                        rf"^- {criterion}：.*$",
                        f"- {criterion}：{choice}",
                        text,
                        flags=re.MULTILINE,
                    )
                text = text.replace("- 评审人代号：", f"- 评审人代号：读者{reader}")
                text = text.replace("- 评审日期：", "- 评审日期：2026-07-21")
                for dimension, maximum in (
                    ("对谈现场与人物原声（20）", 20),
                    ("阅读导航与节奏（10）", 10),
                    ("排版与运营完成度（5）", 5),
                ):
                    text = re.sub(
                        rf"^\| {re.escape(dimension)} \| [^|]+ \| [^|]+ \| [^|]+ \|$",
                        f"| {dimension} | {maximum} | {maximum} | {maximum - 2} |",
                        text,
                        flags=re.MULTILINE,
                    )
                form.write_text(text, encoding="utf-8")
            source_form = output / "来源评审/来源评审表.md"
            source_text = source_form.read_text(encoding="utf-8")
            source_text = source_text.replace("- 评审状态：待填写", "- 评审状态：已完成")
            source_text = source_text.replace("- 输入一致性：待确认", "- 输入一致性：是")
            source_text = source_text.replace("- 来源评审人：待填", "- 来源评审人：编辑甲")
            source_text = source_text.replace("- 评审日期：待填", "- 评审日期：2026-07-21")
            source_text = source_text.replace(
                "- 是否允许进入读者体验总分比较：待确认",
                "- 是否允许进入读者体验总分比较：是",
            )
            source_form.write_text(source_text, encoding="utf-8")

            incomplete = summarize_package(output)
            self.assertFalse(incomplete["evidence_ready"])
            self.assertFalse(incomplete["source_form_valid"])

            for blocker in (
                "编造事实",
                "说话人归属错误",
                "A级内容静默遗漏",
                "未标记低置信度内容",
                "已确认专名错写",
            ):
                source_text = re.sub(
                    rf"^\| {re.escape(blocker)} \| [^|]+ \| [^|]+ \| [^|]*\|$",
                    f"| {blocker} | 0 | 0 |  |",
                    source_text,
                    flags=re.MULTILINE,
                )
            source_text = re.sub(
                r"^\| 忠实与可追溯（35） \| [^|]*\| [^|]*\| [^|]*\|$",
                "| 忠实与可追溯（35） | 34 | 32 |  |",
                source_text,
                flags=re.MULTILINE,
            )
            source_text = re.sub(
                r"^\| 原始内容完整度（30） \| [^|]*\| [^|]*\| [^|]*\|$",
                "| 原始内容完整度（30） | 29 | 27 |  |",
                source_text,
                flags=re.MULTILINE,
            )
            source_form.write_text(source_text, encoding="utf-8")

            result = summarize_package(output)
            self.assertTrue(result["evidence_ready"])
            self.assertEqual(3, result["valid_reader_count"])
            self.assertEqual(manifest["private_mapping"]["A"], result["winner"])
            self.assertEqual(
                manifest["private_mapping"]["A"] == "first",
                result["single_case_primary_superior"],
            )

            source_form.write_text(
                source_text.replace(
                    "| 编造事实 | 0 | 0 |  |",
                    "| 编造事实 | 有 | 0 | 证据时间码 |",
                ),
                encoding="utf-8",
            )
            blocked = summarize_package(output)
            self.assertFalse(blocked["evidence_ready"])
            self.assertTrue(any(blocked["source_blockers"].values()))

    def test_conditional_release_can_pass_with_explicit_condition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            report_path = case / "outputs" / "03_编辑质检报告.md"
            report_path.write_text(
                REPORT.replace(
                    "- 是否可发布：是",
                    "- 是否可发布：有条件可发布\n- 发布条件：关闭 P1 并补齐运营素材。",
                ),
                encoding="utf-8",
            )
            findings = audit_case(case)
            self.assertFalse(any(item.level == "ERROR" for item in findings))
            self.assertIn("CONDITIONAL_RELEASE", {item.code for item in findings})

    def test_pending_final_review_can_pass_mechanical_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            report_path = case / "outputs" / "03_编辑质检报告.md"
            report_path.write_text(
                REPORT.replace(
                    "- 是否可发布：是",
                    "- 是否可发布：待人工终审\n- 发布条件：关闭 P1 并完成随机抽听。",
                ),
                encoding="utf-8",
            )
            findings = audit_case(case)
            self.assertFalse(any(item.level == "ERROR" for item in findings))
            self.assertIn("PENDING_FINAL_REVIEW", {item.code for item in findings})

    def test_release_mode_rejects_ai_precheck_without_signoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            findings = audit_case(case)
            codes = {item.code for item in audit_release_readiness(case, findings)}
            self.assertIn("RELEASE_NO_HUMAN_REVIEW", codes)
            self.assertIn("RELEASE_MISSING_SIGNOFF", codes)

    def test_release_mode_accepts_human_reviewed_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            freeze_baseline(case)
            summarize_human_review(case, "编辑甲", "2026-07-20", 28)
            report_path = case / "outputs" / "03_编辑质检报告.md"
            report_path.write_text(
                REPORT.replace("AI预评", "人工终审")
                .replace("人工终审耗时：待执行", "人工终审耗时：28分钟")
                .replace("总耗时：待终审后填写", "总耗时：2小时28分钟")
                .replace("发布稿独立通读：AI流程预检已完成", "发布稿独立通读：真人已完成")
                .replace("说话人随机抽查：待执行", "说话人随机抽查：已完成")
                + "\n- 终审人：编辑甲\n- 终审日期：2026-07-20\n",
                encoding="utf-8",
            )
            findings = audit_case(case)
            self.assertEqual([], audit_release_readiness(case, findings))

    def test_release_mode_rejects_stale_human_review_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            freeze_baseline(case)
            summarize_human_review(case, "编辑甲", "2026-07-20", 28)
            draft_path = case / "outputs/01_公众号可发布稿.md"
            draft_path.write_text(
                draft_path.read_text(encoding="utf-8") + "\n终审后又发生变化。\n",
                encoding="utf-8",
            )
            findings = audit_case(case)
            codes = {item.code for item in audit_release_readiness(case, findings)}
            self.assertIn("RELEASE_REVIEW_METRICS_DRIFT", codes)

    def test_human_superiority_claim_requires_dual_track_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            report_path = case / "outputs" / "03_编辑质检报告.md"
            report_path.write_text(
                REPORT.replace("无人工成稿独立整理", "双轨对比")
                .replace("AI预评", "独立双轨评审")
                .replace("对比结论：未形成", "对比结论：AI优于人工"),
                encoding="utf-8",
            )
            self.assertIn("MISSING_DUAL_TRACK_EVIDENCE", self.error_codes(case))

    def test_human_superiority_claim_rejects_report_only_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            report_path = case / "outputs" / "03_编辑质检报告.md"
            report_path.write_text(
                REPORT.replace("无人工成稿独立整理", "双轨对比")
                .replace("AI预评", "独立双轨评审")
                .replace(
                    "对比结论：未形成",
                    "对比结论：AI优于人工\n"
                    "- 输入一致性：是\n"
                    "- 来源评审人：编辑甲\n"
                    "- 读者评审人：读者组\n"
                    "- 有效读者人数：3",
                ),
                encoding="utf-8",
            )
            codes = self.error_codes(case)
            self.assertNotIn("MISSING_DUAL_TRACK_EVIDENCE", codes)
            self.assertIn("MISSING_DUAL_TRACK_PACKAGE_EVIDENCE", codes)

    def test_human_superiority_claim_accepts_bound_completed_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = self.make_case(root)
            comparison = root / "人工稿.md"
            comparison.write_text(
                DRAFT.replace("每天抽出一小时学习", "每天留出固定时间学习"),
                encoding="utf-8",
            )
            package = case / "_work/双轨匿名评审包"
            campaign = self.make_campaign_manifest(case, root / "campaign.json")
            manifest = prepare_review_package(
                case,
                case / "outputs/01_公众号可发布稿.md",
                comparison,
                package,
                readers=3,
                campaign_manifest=campaign,
            )
            primary_label = next(
                label
                for label, track in manifest["private_mapping"].items()
                if track == "first"
            )
            for reader_dir in sorted(package.glob("R[0-9][0-9]")):
                form = reader_dir / "03_匿名评审表.md"
                text = form.read_text(encoding="utf-8").replace(
                    "- 评审状态：待填写", "- 评审状态：已完成"
                )
                for criterion in (
                    "更像在听直播",
                    "更愿意读完",
                    "人物更鲜活",
                    "长但不拖沓",
                    "更适合直接发布",
                    "最终偏好",
                ):
                    text = re.sub(
                        rf"^- {criterion}：.*$",
                        f"- {criterion}：{primary_label}",
                        text,
                        flags=re.MULTILINE,
                    )
                for dimension, maximum in (
                    ("对谈现场与人物原声（20）", 20),
                    ("阅读导航与节奏（10）", 10),
                    ("排版与运营完成度（5）", 5),
                ):
                    scores = (
                        (maximum, maximum - 2)
                        if primary_label == "A"
                        else (maximum - 2, maximum)
                    )
                    text = re.sub(
                        rf"^\| {re.escape(dimension)} \| [^|]+ \| [^|]+ \| [^|]+ \|$",
                        f"| {dimension} | {maximum} | {scores[0]} | {scores[1]} |",
                        text,
                        flags=re.MULTILINE,
                    )
                text = text.replace(
                    "- 评审人代号：", f"- 评审人代号：{reader_dir.name}"
                ).replace("- 评审日期：", "- 评审日期：2026-07-21")
                form.write_text(text, encoding="utf-8")

            source_form = package / "来源评审/来源评审表.md"
            source_text = source_form.read_text(encoding="utf-8")
            source_text = (
                source_text.replace("- 评审状态：待填写", "- 评审状态：已完成")
                .replace("- 输入一致性：待确认", "- 输入一致性：是")
                .replace("- 来源评审人：待填", "- 来源评审人：编辑甲")
                .replace("- 评审日期：待填", "- 评审日期：2026-07-21")
                .replace(
                    "- 是否允许进入读者体验总分比较：待确认",
                    "- 是否允许进入读者体验总分比较：是",
                )
            )
            for blocker in (
                "编造事实",
                "说话人归属错误",
                "A级内容静默遗漏",
                "未标记低置信度内容",
                "已确认专名错写",
            ):
                source_text = re.sub(
                    rf"^\| {re.escape(blocker)} \| [^|]+ \| [^|]+ \| [^|]*\|$",
                    f"| {blocker} | 0 | 0 |  |",
                    source_text,
                    flags=re.MULTILINE,
                )
            source_values = {
                "忠实与可追溯（35）": (34, 32),
                "原始内容完整度（30）": (29, 27),
            }
            for dimension, preferred_scores in source_values.items():
                scores = (
                    preferred_scores
                    if primary_label == "A"
                    else tuple(reversed(preferred_scores))
                )
                source_text = re.sub(
                    rf"^\| {re.escape(dimension)} \| [^|]*\| [^|]*\| [^|]*\|$",
                    f"| {dimension} | {scores[0]} | {scores[1]} |  |",
                    source_text,
                    flags=re.MULTILINE,
                )
            source_form.write_text(source_text, encoding="utf-8")
            review_result = summarize_package(package)
            self.assertTrue(
                review_result["single_case_primary_superior"], review_result
            )

            report_path = case / "outputs/03_编辑质检报告.md"
            report_path.write_text(
                REPORT.replace("无人工成稿独立整理", "双轨对比")
                .replace("AI预评", "独立双轨评审")
                .replace(
                    "对比结论：未形成",
                    "对比结论：AI优于人工\n"
                    "- 输入一致性：是\n"
                    "- 来源评审人：编辑甲\n"
                    "- 读者评审人：读者组\n"
                    "- 有效读者人数：3",
                ),
                encoding="utf-8",
            )
            self.assertNotIn(
                "MISSING_DUAL_TRACK_PACKAGE_EVIDENCE", self.error_codes(case)
            )
            campaign_text = campaign.read_text(encoding="utf-8")
            campaign.write_text(campaign_text + "\n", encoding="utf-8")
            self.assertIn(
                "MISSING_DUAL_TRACK_PACKAGE_EVIDENCE", self.error_codes(case)
            )
            campaign.write_text(campaign_text, encoding="utf-8")
            self.assertNotIn(
                "MISSING_DUAL_TRACK_PACKAGE_EVIDENCE", self.error_codes(case)
            )
            manifest_path = package / "manifest.json"
            exposed = json.loads(manifest_path.read_text(encoding="utf-8"))
            exposed["experiment_type"] = "已知标杆回归"
            exposed["reference_exposure"] = "生成阶段已见对照稿"
            manifest_path.write_text(
                json.dumps(exposed, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.assertIn(
                "MISSING_DUAL_TRACK_PACKAGE_EVIDENCE", self.error_codes(case)
            )

    def test_new_number_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(DRAFT + "\n她影响了 9999 人。\n", encoding="utf-8")
            self.assertIn("NEW_NUMBER", self.error_codes(case))

    def test_chinese_number_can_be_normalized_to_arabic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(DRAFT.replace("过去十年", "过去10年"), encoding="utf-8")
            self.assertNotIn("NEW_NUMBER", self.error_codes(case))

    def test_header_clock_time_does_not_break_speaker_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            source_path = case / "inputs" / "02_带时间码原稿.md"
            source_path.write_text("导出时间\n2026年07月17日 23:37\n" + SOURCE, encoding="utf-8")
            self.assertNotIn("TIMECODE_ORDER", self.error_codes(case))

    def test_short_recent_year_can_expand_to_four_digits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            source_path = case / "inputs" / "02_带时间码原稿.md"
            source_path.write_text(
                "导出时间\n2026年07月18日\n" + SOURCE.replace("过去十年", "13年开始写作，过去十年"),
                encoding="utf-8",
            )
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(DRAFT + "\n她在2013年开始写作。\n", encoding="utf-8")
            self.assertNotIn("NEW_NUMBER", self.error_codes(case))

    def test_unfinished_placeholder_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(DRAFT + "\n【待确认人名】\n", encoding="utf-8")
            self.assertIn("UNFINISHED_PLACEHOLDER", self.error_codes(case))

    def test_duplicate_long_paragraph_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            repeated = (
                "这是一段用于检查重复的长内容，它保留了人物的具体场景、完整判断链和读者可执行方法，"
                "也保留了事件发生的时间、地点、行动和结果，足以构成一个能独立理解的完整段落，"
                "不应该在同一篇发布稿里被完整复制两次。"
            )
            draft_path.write_text(
                DRAFT + f"\n\n{repeated}\n\n{repeated}\n", encoding="utf-8"
            )
            self.assertIn("DUPLICATE_PARAGRAPH", self.error_codes(case))

    def test_publication_must_keep_ninety_percent_of_full_draft_dialogue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            extra_turns = (
                "\n\n**主持人：** 这里还有一条完整追问，不能只因为需要缩短篇幅就删除。"
                "\n\n**示例嘉宾：** 这里保留了人物的具体经历、现场反应、原始比喻和回答中的限定条件。"
                * 8
            )
            (case / "_work" / "完整编辑底稿.md").write_text(
                DRAFT + extra_turns, encoding="utf-8"
            )
            self.assertIn("PUBLISHED_DROPS_FULL_DRAFT", self.error_codes(case))

    def test_publication_can_match_full_draft_without_forced_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            (case / "_work" / "完整编辑底稿.md").write_text(DRAFT, encoding="utf-8")
            self.assertNotIn("PUBLISHED_DROPS_FULL_DRAFT", self.error_codes(case))

    def test_full_draft_must_contain_named_dialogue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            (case / "_work" / "完整编辑底稿.md").write_text(
                "# 完整编辑底稿\n\n只有摘要，没有带署名的问答。\n", encoding="utf-8"
            )
            self.assertIn("FULL_DRAFT_NO_DIALOGUE", self.error_codes(case))

    def test_confirmed_nonstandard_term_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            terms = """# 人物与专名表

| 规范写法 | 类别 | 必须替换的错写 | 可接受写法 | 状态 | 核验依据 |
| --- | --- | --- | --- | --- | --- |
| 示例社群 | 社群 | 示例社裙、示例社群错称 | — | 已确认 | 运营确认 |
"""
            (case / "inputs" / "03_人物与专名表.md").write_text(terms, encoding="utf-8")
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(DRAFT + "\n他后来加入示例社裙。\n", encoding="utf-8")
            self.assertIn("NONSTANDARD_TERM", self.error_codes(case))

    def test_pending_term_variant_does_not_block_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            terms = """# 人物与专名表

| 规范写法 | 类别 | 必须替换的错写 | 可接受写法 | 状态 | 核验依据 |
| --- | --- | --- | --- | --- | --- |
| 示例嘉宾说 | 视频号 | 小雪说 | — | 待确认 | 两种写法并存 |
"""
            (case / "inputs" / "03_人物与专名表.md").write_text(terms, encoding="utf-8")
            draft_path = case / "outputs" / "01_公众号可发布稿.md"
            draft_path.write_text(DRAFT + "\n内容来自小雪说。\n", encoding="utf-8")
            self.assertNotIn("NONSTANDARD_TERM", self.error_codes(case))

    def test_missing_evidence_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            (case / "_work" / "证据映射.json").unlink()
            self.assertIn("MISSING_FILE", self.error_codes(case))

    def test_long_interview_evidence_cannot_stop_at_chapter_level(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            source_path = case / "inputs" / "02_带时间码原稿.md"
            source_path.write_text(
                SOURCE + "\n" + ("这是用于模拟长访谈的具体场景、追问、回答和限定条件。" * 500),
                encoding="utf-8",
            )
            self.assertIn("EVIDENCE_TOO_COARSE", self.error_codes(case))

    def test_long_interview_evidence_requires_draft_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            source_path = case / "inputs" / "02_带时间码原稿.md"
            source_path.write_text(
                SOURCE + "\n" + ("这是用于模拟长访谈的具体场景、追问、回答和限定条件。" * 500),
                encoding="utf-8",
            )
            evidence = json.loads(json.dumps(EVIDENCE, ensure_ascii=False))
            evidence["sections"] = [
                {
                    "module_id": f"M{index:02d}",
                    "draft_heading": "01 过去的十年",
                    "draft_anchor": "这句定位文字并不存在于成稿",
                    "source_ranges": [{"start": "00:10", "end": "00:40"}],
                    "key_details": ["过去十年"],
                    "verification_status": "已核对原稿",
                    "edit_decision_ids": [],
                    "notes": "测试",
                }
                for index in range(1, 9)
            ]
            (case / "_work" / "证据映射.json").write_text(
                json.dumps(evidence, ensure_ascii=False), encoding="utf-8"
            )
            self.assertIn("EVIDENCE_DRAFT_ANCHOR", self.error_codes(case))

    def test_evidence_must_link_every_nontrivial_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            source_path = case / "inputs" / "02_带时间码原稿.md"
            source_path.write_text(
                SOURCE + "\n" + ("这是用于模拟长访谈的具体场景、追问、回答和限定条件。" * 500),
                encoding="utf-8",
            )
            ledger = json.loads(json.dumps(EDIT_LEDGER, ensure_ascii=False))
            ledger["nontrivial_edits"] = [
                {
                    "id": "E001",
                    "start": "00:10",
                    "end": "00:10",
                    "type": "隐私处理",
                    "source_excerpt": "我是示例嘉宾",
                    "draft_excerpt": "我是示例嘉宾",
                    "basis": "隐私规则",
                    "confidence": "已确认",
                    "status": "已解决",
                    "notes": "测试",
                }
            ]
            (case / "_work" / "编辑决策台账.json").write_text(
                json.dumps(ledger, ensure_ascii=False), encoding="utf-8"
            )
            evidence = json.loads(json.dumps(EVIDENCE, ensure_ascii=False))
            evidence["sections"] = [
                {
                    "module_id": f"M{index:02d}",
                    "draft_heading": "01 过去的十年",
                    "draft_anchor": "过去十年，我一直在照顾家庭",
                    "source_ranges": [{"start": "00:10", "end": "00:40"}],
                    "key_details": ["过去十年"],
                    "verification_status": "已核对原稿",
                    "edit_decision_ids": [],
                    "notes": "测试",
                }
                for index in range(1, 9)
            ]
            (case / "_work" / "证据映射.json").write_text(
                json.dumps(evidence, ensure_ascii=False), encoding="utf-8"
            )
            self.assertIn("EVIDENCE_UNMAPPED_EDITS", self.error_codes(case))

    def test_evidence_and_coverage_timeline_must_agree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            source_path = case / "inputs" / "02_带时间码原稿.md"
            source_path.write_text(
                SOURCE + "\n" + ("这是用于模拟长访谈的具体场景、追问、回答和限定条件。" * 500),
                encoding="utf-8",
            )
            evidence = json.loads(json.dumps(EVIDENCE, ensure_ascii=False))
            evidence["sections"] = [
                {
                    "module_id": f"M{index:02d}",
                    "draft_heading": "01<br>过去的十年<br>她如何重新找回自己",
                    "draft_anchor": "我是示例嘉宾，过去十年我一直在照顾家庭",
                    "source_ranges": [{"start": "00:10", "end": "00:40"}],
                    "key_details": ["过去十年"],
                    "verification_status": "已核对原稿",
                    "edit_decision_ids": [],
                    "notes": "测试",
                }
                for index in range(1, 9)
            ]
            (case / "_work" / "证据映射.json").write_text(
                json.dumps(evidence, ensure_ascii=False), encoding="utf-8"
            )
            self.assertIn("EVIDENCE_COVERAGE_DRIFT", self.error_codes(case))

    def test_long_overcompressed_draft_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            short_draft = """# 重新开始

## 01<br>重新开始

**主持人：** 你怎么改变？

**示例嘉宾：** 我每天学习，再重新开始。
"""
            (case / "outputs" / "01_公众号可发布稿.md").write_text(
                short_draft, encoding="utf-8"
            )
            source_path = case / "inputs" / "02_带时间码原稿.md"
            source_path.write_text(
                SOURCE + "\n" + ("这是有具体场景、主持人追问、人物反应和限定条件的原稿内容。" * 500),
                encoding="utf-8",
            )
            self.assertIn("OVER_COMPRESSED_IMMERSIVE_DRAFT", self.error_codes(case))

    def test_coverage_map_cannot_excuse_overcompression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_case(Path(directory))
            source_path = case / "inputs" / "02_带时间码原稿.md"
            source_path.write_text(
                SOURCE + "\n" + ("这是有具体场景、主持人追问、人物反应和限定条件的原稿内容。" * 500),
                encoding="utf-8",
            )
            codes = self.error_codes(case)
            self.assertIn("OVER_COMPRESSED_IMMERSIVE_DRAFT", codes)
            self.assertIn("SCORE_CAP_OVER_COMPRESSION", codes)



if __name__ == "__main__":
    unittest.main()
