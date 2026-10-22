from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from audit_solo_share import audit_case


REQUIRED_DIRS = ("inputs", "outputs", "_work")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_case(root: Path) -> Path:
    case = root / "2026-07-28-单人开营分享"
    for directory in REQUIRED_DIRS:
        (case / directory).mkdir(parents=True, exist_ok=True)
    blocks = [
        ("00:01", "开场预期这是第一段有效分享内容，保留原声和具体说明。"),
        ("10:00", "万事问AI这是第二段有效分享内容，保留日常问题和追问。"),
        ("20:00", "提示词这是第三段有效分享内容，保留目标边界和反馈。"),
        ("30:00", "抛砖引玉这是第四段有效分享内容，保留读书听课例子。"),
        ("40:00", "能力框架这是第五段有效分享内容，保留复杂任务实践。"),
        ("48:00", "真实结尾这是第六段有效分享内容，感谢同学并回到群里。"),
    ]
    source = ["单人测试原稿"]
    for timecode, body in blocks:
        source.extend([f"发言人1   {timecode}", body, ""])
    write(case / "inputs/02_带时间码原稿.md", "\n".join(source))
    write(case / "inputs/03_人物与专名表.md", "# 人物与专名表\n")

    body = "\n\n".join(text for _, text in blocks)
    draft = (
        "# 单人分享原声整理\n\n"
        "<!-- SOLO_BODY_START -->\n\n"
        "**示例主持：** "
        + body
        + "\n\n<!-- SOLO_BODY_END -->\n"
    )
    write(case / "outputs/01_公众号可发布稿.md", draft)
    write(
        case / "outputs/02_待核问题清单.md",
        "# 待核问题清单\n\n"
        "## 汇总\n\n- P0：0\n- P1：0\n- P2：0\n\n"
        "| 级别 | 时间码 | 问题类型 | 原文/成稿 | 处理建议 | 状态 |\n"
        "| --- | --- | --- | --- | --- | --- |\n",
    )
    write(
        case / "outputs/03_编辑质检报告.md",
        "# 编辑质检报告\n\n"
        "- 内容类型：单人开营分享\n"
        "- 评分性质：AI预评\n"
        "- 终审人：待填\n"
        "- 终审日期：待填\n"
        "- 学习稿独立通读：待执行\n",
    )
    write(
        case / "_work/完整编辑底稿.md",
        "# 完整编辑底稿\n\n<!-- SOLO_BODY_START -->\n\n"
        + body
        + "\n\n<!-- SOLO_BODY_END -->\n",
    )
    coverage_rows = [
        "| 00:01–09:59 | 开场 | A | 保留 | 第一章 |",
        "| 10:00–19:59 | 提问 | A | 保留 | 第二章 |",
        "| 20:00–29:59 | 提示词 | A | 保留 | 第三章 |",
        "| 30:00–39:59 | 学习 | A | 保留 | 第四章 |",
        "| 40:00–48:00 | 实践 | A | 保留 | 第五章 |",
    ]
    write(
        case / "_work/内容覆盖表.md",
        "# 内容覆盖表\n\n| 时间段 | 话题 | 级别 | 处理 | 章节 |\n"
        "| --- | --- | --- | --- | --- |\n"
        + "\n".join(coverage_rows)
        + "\n",
    )
    sections = []
    for index, (timecode, anchor) in enumerate(blocks, start=1):
        sections.append(
            {
                "module_id": f"M{index:02d}",
                "draft_heading": f"第{index}章",
                "draft_anchor": anchor,
                "source_ranges": [{"start": timecode, "end": timecode}],
                "key_details": [anchor],
                "verification_status": "原稿核对",
                "edit_decision_ids": [],
                "notes": "按原顺序轻度校订",
            }
        )
    write(
        case / "_work/证据映射.json",
        json.dumps({"version": 1, "source": "inputs/02_带时间码原稿.md", "sections": sections}, ensure_ascii=False),
    )
    write(
        case / "_work/编辑决策台账.json",
        json.dumps({"version": 1, "source": "inputs/02_带时间码原稿.md", "nontrivial_edits": []}, ensure_ascii=False),
    )
    return case


def add_routed_solo_bundle(case: Path) -> None:
    replay = case / "inputs/01_直播回放.mp4"
    audio = case / "outputs/04_整场音频.m4a"
    source = case / "inputs/02_带时间码原稿.md"
    replay.write_bytes(b"source-video")
    audio.write_bytes(b"derived-audio")
    write(
        case / "_work/场次分流.json",
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "mode": "solo",
                "target_skill": "references/solo.md",
                "source": {
                    "path": "inputs/02_带时间码原稿.md",
                    "sha256": sha256(source),
                },
                "replay": {
                    "path": "inputs/01_直播回放.mp4",
                    "sha256": sha256(replay),
                    "evidence": [f"{index}:00｜仅讲者" for index in range(5)],
                },
            },
            ensure_ascii=False,
        ),
    )
    write(
        case / "_work/整场音频.json",
        json.dumps(
            {
                "schema_version": 1,
                "source": {
                    "path": "inputs/01_直播回放.mp4",
                    "sha256": sha256(replay),
                    "duration_seconds": 100.0,
                },
                "output": {
                    "path": "outputs/04_整场音频.m4a",
                    "sha256": sha256(audio),
                    "duration_seconds": 100.5,
                },
            },
            ensure_ascii=False,
        ),
    )


class AuditSoloShareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.case = make_case(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def codes(self, *, share: bool = False, release: bool = False) -> str:
        return "\n".join(audit_case(self.case, share=share, release=release).errors)

    def test_valid_package_passes(self) -> None:
        self.assertEqual([], audit_case(self.case).errors)

    def test_rejects_second_source_speaker(self) -> None:
        path = self.case / "inputs/02_带时间码原稿.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n发言人2   49:00\n插话\n", encoding="utf-8")
        self.assertIn("NOT_EXACTLY_ONE_SPEAKER", self.codes())

    def test_rejects_non_increasing_timecodes(self) -> None:
        path = self.case / "inputs/02_带时间码原稿.md"
        text = path.read_text(encoding="utf-8").replace("发言人1   20:00", "发言人1   09:00")
        path.write_text(text, encoding="utf-8")
        self.assertIn("TIMECODES_NOT_INCREASING", self.codes())

    def test_rejects_missing_required_delivery(self) -> None:
        (self.case / "_work/证据映射.json").unlink()
        self.assertIn("MISSING_REQUIRED_FILE", self.codes())

    def test_rejects_low_source_retention(self) -> None:
        path = self.case / "outputs/01_公众号可发布稿.md"
        path.write_text(
            "# 单人分享\n\n<!-- SOLO_BODY_START -->\n\n**示例主持：** 只有一句。\n\n<!-- SOLO_BODY_END -->\n",
            encoding="utf-8",
        )
        self.assertIn("SOURCE_RETENTION_LOW", self.codes())

    def test_rejects_low_full_draft_retention(self) -> None:
        path = self.case / "_work/完整编辑底稿.md"
        text = path.read_text(encoding="utf-8").replace(
            "<!-- SOLO_BODY_END -->", ("额外有效内容" * 80) + "\n<!-- SOLO_BODY_END -->"
        )
        path.write_text(text, encoding="utf-8")
        self.assertIn("FULL_DRAFT_RETENTION_LOW", self.codes())

    def test_rejects_ten_minute_coverage_gap(self) -> None:
        path = self.case / "_work/内容覆盖表.md"
        text = path.read_text(encoding="utf-8").replace(
            "| 20:00–29:59 | 提示词 | A | 保留 | 第三章 |\n", ""
        )
        path.write_text(text, encoding="utf-8")
        self.assertIn("TEN_MINUTE_COVERAGE_GAP", self.codes())

    def test_rejects_fewer_than_six_evidence_modules(self) -> None:
        path = self.case / "_work/证据映射.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["sections"] = data["sections"][:5]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertIn("EVIDENCE_MODULES_LOW", self.codes())

    def test_rejects_untracked_title_number(self) -> None:
        path = self.case / "outputs/01_公众号可发布稿.md"
        text = path.read_text(encoding="utf-8").replace("# 单人分享原声整理", "# 14天单人分享")
        path.write_text(text, encoding="utf-8")
        self.assertIn("TITLE_NUMBER_UNVERIFIED", self.codes())

    def test_rejects_open_p0(self) -> None:
        path = self.case / "outputs/02_待核问题清单.md"
        text = path.read_text(encoding="utf-8") + "| P0 | 01:00 | 数字 | 一处数字 | 删除 | 待确认 |\n"
        path.write_text(text, encoding="utf-8")
        self.assertIn("OPEN_P0", self.codes())

    def test_rejects_p1_without_clip_or_conservative_expression(self) -> None:
        path = self.case / "outputs/02_待核问题清单.md"
        text = path.read_text(encoding="utf-8") + "| P1 | 22:48 | 工具名 | 疑似词 | 等待回听 | 待确认 |\n"
        path.write_text(text, encoding="utf-8")
        self.assertIn("P1_WITHOUT_EVIDENCE_OR_CONSERVATIVE_TEXT", self.codes())

    def test_share_requires_human_gate(self) -> None:
        errors = self.codes(share=True)
        self.assertIn("SHARE_HUMAN_REVIEW_REQUIRED", errors)
        self.assertIn("SHARE_REVIEWER_REQUIRED", errors)
        self.assertIn("SHARE_DATE_REQUIRED", errors)
        self.assertIn("SHARE_READTHROUGH_REQUIRED", errors)
        self.assertIn("SHARE_BASELINE_REQUIRED", errors)

    def test_release_is_compatible_alias_for_share_gate(self) -> None:
        self.assertEqual(self.codes(share=True), self.codes(release=True))

    def test_routed_solo_requires_full_audio(self) -> None:
        write(
            self.case / "_work/场次分流.json",
            json.dumps({"status": "PASS", "mode": "solo"}, ensure_ascii=False),
        )
        errors = self.codes()
        self.assertIn("SOLO_FULL_AUDIO_REQUIRED", errors)
        self.assertIn("SOLO_AUDIO_MANIFEST_REQUIRED", errors)

    def test_routed_solo_forbids_speaker_prefix(self) -> None:
        add_routed_solo_bundle(self.case)
        self.assertIn("SOLO_SPEAKER_PREFIX_FORBIDDEN", self.codes())

    def test_routed_solo_without_prefix_and_with_audio_passes(self) -> None:
        add_routed_solo_bundle(self.case)
        path = self.case / "outputs/01_公众号可发布稿.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace("**示例主持：** ", ""),
            encoding="utf-8",
        )
        self.assertEqual([], audit_case(self.case).errors)


if __name__ == "__main__":
    unittest.main()
