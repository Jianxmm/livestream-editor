from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import hashlib
import tempfile
import unittest
from pathlib import Path

from extract_full_audio import find_replay, verify_manifest


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExtractFullAudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.case = Path(self.temp.name) / "2026-07-30-单人"
        (self.case / "inputs").mkdir(parents=True)
        (self.case / "outputs").mkdir()
        self.replay = self.case / "inputs/01_直播回放.mp4"
        self.audio = self.case / "outputs/04_整场音频.m4a"
        self.replay.write_bytes(b"source-video")
        self.audio.write_bytes(b"derived-audio")
        self.manifest = {
            "source": {
                "path": "inputs/01_直播回放.mp4",
                "sha256": sha256(self.replay),
                "duration_seconds": 100.0,
            },
            "output": {
                "path": "outputs/04_整场音频.m4a",
                "sha256": sha256(self.audio),
                "duration_seconds": 100.5,
            },
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_finds_exactly_one_replay(self) -> None:
        self.assertEqual(self.replay, find_replay(self.case))

    def test_rejects_multiple_replays(self) -> None:
        (self.case / "inputs/01_直播回放.mov").write_bytes(b"other")
        with self.assertRaises(ValueError):
            find_replay(self.case)

    def test_valid_manifest_passes(self) -> None:
        verify_manifest(self.case, self.manifest)

    def test_changed_audio_hash_fails(self) -> None:
        self.audio.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            verify_manifest(self.case, self.manifest)

    def test_duration_mismatch_fails(self) -> None:
        self.manifest["output"]["duration_seconds"] = 80.0
        with self.assertRaises(ValueError):
            verify_manifest(self.case, self.manifest)

    def test_invalid_manifest_shape_fails(self) -> None:
        with self.assertRaises(ValueError):
            verify_manifest(self.case, [])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
