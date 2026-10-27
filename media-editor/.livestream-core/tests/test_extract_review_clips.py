#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import unittest

from extract_review_clips import (
    parse_review_clips,
    render_review_sheet,
    share_duplicate_windows,
    split_window,
)


ISSUES = """# 待核问题清单

| 级别 | 时间码 | 问题类型 | 原文/成稿 | 处理建议 | 状态 |
| --- | --- | --- | --- | --- | --- |
| P1 | 02:47–03:26 | 身份/数字 | 年龄和时间 | 回听 | 待确认 |
| P1 | 35:00、44:23 | 营收 | 两处数字 | 回听 | 待确认 |
| P1 | 01:03:47–01:09:03 | 产品 | 长范围 | 回听 | 待确认 |
| P1 | 02:22:23–02:22:44 | 说话人 | 已核实 | — | 已解决 |
| P2 | 全程 | 运营 | 无时间码 | — | 待确认 |
"""


class ExtractReviewClipsTest(unittest.TestCase):
    def test_single_and_multiple_timestamps_are_parsed(self) -> None:
        clips = parse_review_clips(ISSUES, padding=5, max_duration=90)
        self.assertTrue(any(clip.start == 162 for clip in clips))
        self.assertTrue(any(clip.start == 2095 for clip in clips))
        self.assertTrue(any(clip.start == 2658 for clip in clips))
        self.assertFalse(any("02h22m" in clip.filename for clip in clips))

    def test_long_range_is_split_into_bounded_clips(self) -> None:
        windows = split_window(3600, 3900, padding=5, max_duration=90)
        self.assertGreater(len(windows), 1)
        self.assertTrue(all(duration <= 90 for _, duration in windows))
        self.assertEqual(3595, windows[0][0])

    def test_review_sheet_groups_clips_by_issue(self) -> None:
        clips = parse_review_clips(ISSUES, padding=5, max_duration=90)
        sheet = render_review_sheet(clips)
        self.assertIn("| 2 | 35:00、44:23 |", sheet)
        self.assertIn("P1-02-01", sheet)
        self.assertIn("P1-02-02", sheet)
        self.assertEqual(1, sheet.count("| 2 | 35:00、44:23 |"))

    def test_same_time_window_reuses_one_audio_file(self) -> None:
        issues = """| P1 | 03:26–04:29 | 身份 | 年龄 | 回听 | 待确认 |
| P1 | 03:26–04:29 | 业绩 | 人数 | 回听 | 待确认 |
"""
        clips = share_duplicate_windows(
            parse_review_clips(issues, padding=5, max_duration=90)
        )
        self.assertEqual(2, len(clips))
        self.assertEqual(clips[0].filename, clips[1].filename)


if __name__ == "__main__":
    unittest.main()
