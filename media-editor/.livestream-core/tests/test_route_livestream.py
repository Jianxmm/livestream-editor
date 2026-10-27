from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import json
import tempfile
import unittest
from pathlib import Path

from route_livestream import build_route, write_route
from route_fixture import observations_for


class RouteLivestreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.case = Path(self.temp.name) / "2026-07-30-测试"
        (self.case / "inputs").mkdir(parents=True)
        (self.case / "inputs/01_直播回放.mp4").write_bytes(b"video")
        (self.case / "inputs/02_带时间码原稿.md").write_text(
            "发言人1   00:00\n开场\n\n发言人2   10:00\n回应\n",
            encoding="utf-8",
        )
        self.evidence = [f"{index}:00｜回放证据" for index in range(5)]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_acoustic_count_routes_to_solo_even_with_two_labels(self) -> None:
        route = build_route(
            self.case, acoustic_speakers="1", evidence=self.evidence, observations=observations_for(self.case)
        )
        self.assertEqual("solo", route["mode"])
        self.assertEqual("references/solo.md", route["target_skill"])
        self.assertTrue(route["warnings"])

    def test_two_acoustic_speakers_route_to_dual(self) -> None:
        route = build_route(
            self.case, acoustic_speakers="2", evidence=self.evidence, observations=observations_for(self.case)
        )
        self.assertEqual("dual", route["mode"])
        self.assertEqual("PASS", route["status"])

    def test_uncertain_route_is_blocked(self) -> None:
        route = build_route(
            self.case, acoustic_speakers="uncertain", evidence=["00:00｜无法确认"]
        )
        self.assertEqual("unsupported", route["mode"])
        self.assertEqual("BLOCKED", route["status"])

    def test_pass_requires_five_replay_evidence_items(self) -> None:
        with self.assertRaises(ValueError):
            build_route(self.case, acoustic_speakers="1", evidence=["00:00｜开场"])

    def test_existing_different_route_is_not_overwritten(self) -> None:
        route = build_route(
            self.case, acoustic_speakers="1", evidence=self.evidence, observations=observations_for(self.case)
        )
        output = write_route(self.case, route)
        before = output.read_text(encoding="utf-8")
        different = json.loads(before)
        different["mode"] = "dual"
        with self.assertRaises(FileExistsError):
            write_route(self.case, different)
        self.assertEqual(before, output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
