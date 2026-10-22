from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sys
import tempfile
import unittest
from pathlib import Path

from build_source_review_index import build_source_review_index

LIVE_INTERVIEW_SCRIPTS = (
    Path(__file__).resolve().parents[1] / "scripts"
)
sys.path.insert(0, str(LIVE_INTERVIEW_SCRIPTS))
from extract_review_clips import parse_review_clips


class BuildSourceReviewIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.case = Path(self.temp.name) / "2026-07-30-测试"
        (self.case / "inputs").mkdir(parents=True)
        (self.case / "outputs").mkdir()
        (self.case / "inputs/02_带时间码原稿.md").write_text(
            "发言人1   00:00\n开场原话。\n\n"
            "发言人1   10:00\n工具名疑句。\n\n"
            "发言人1   20:00\n结尾原话。\n",
            encoding="utf-8",
        )
        (self.case / "outputs/02_待核问题清单.md").write_text(
            "# 待核问题清单\n\n"
            "| 级别 | 时间码 | 问题类型 | 原文/成稿 | 处理建议 | 状态 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| P1 | 10:00–10:20 | 工具名 | 疑似工具名 | 回听 | 待确认 |\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_links_issue_timecode_to_source_excerpt(self) -> None:
        result = build_source_review_index(self.case)
        self.assertEqual(1, result["issues"])
        issue_text = (
            self.case / "outputs/02_待核问题清单.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "[10:00–10:20](../_work/待核原文索引.md#review-001)",
            issue_text,
        )
        index_text = (self.case / "_work/待核原文索引.md").read_text(
            encoding="utf-8"
        )
        self.assertIn('<a id="review-001"></a>', index_text)
        self.assertIn("工具名疑句。", index_text)
        self.assertIn("开场原话。", index_text)
        self.assertIn("结尾原话。", index_text)

    def test_is_idempotent(self) -> None:
        build_source_review_index(self.case)
        first = (
            self.case / "outputs/02_待核问题清单.md"
        ).read_text(encoding="utf-8")
        build_source_review_index(self.case)
        second = (
            self.case / "outputs/02_待核问题清单.md"
        ).read_text(encoding="utf-8")
        self.assertEqual(first, second)

    def test_linked_timecode_remains_compatible_with_clip_parser(self) -> None:
        build_source_review_index(self.case)
        issue_text = (
            self.case / "outputs/02_待核问题清单.md"
        ).read_text(encoding="utf-8")
        clips = parse_review_clips(issue_text)
        self.assertEqual(1, len(clips))
        self.assertEqual(592, clips[0].start)
        self.assertEqual(36, clips[0].duration)

    def test_rejects_issue_without_timecode(self) -> None:
        path = self.case / "outputs/02_待核问题清单.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace("10:00–10:20", "本章"),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            build_source_review_index(self.case)

    def test_skips_resolved_global_item_without_timecode(self) -> None:
        path = self.case / "outputs/02_待核问题清单.md"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "| P2 | 全程 | 转写 | 已处理的全局问题 | 无需回听 | 已解决 |\n",
            encoding="utf-8",
        )
        result = build_source_review_index(self.case)
        self.assertEqual(1, result["issues"])
        self.assertIn(
            "| P2 | 全程 | 转写 | 已处理的全局问题 | 无需回听 | 已解决 |",
            path.read_text(encoding="utf-8"),
        )

    def test_skips_pending_p2_global_item_without_timecode(self) -> None:
        path = self.case / "outputs/02_待核问题清单.md"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "| P2 | 全程 | 运营 | 待补运营素材 | 终审前补充 | 待确认 |\n",
            encoding="utf-8",
        )
        result = build_source_review_index(self.case)
        self.assertEqual(1, result["issues"])
        self.assertIn(
            "| P2 | 全程 | 运营 | 待补运营素材 | 终审前补充 | 待确认 |",
            path.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
