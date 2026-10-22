#!/usr/bin/env python3
"""Extract and verify the required full-length M4A for a routed solo share."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from media_tools import audio_clip, duration


OUTPUT = Path("outputs/04_整场音频.m4a")
MANIFEST = Path("_work/整场音频.json")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_replay(case_dir: Path) -> Path:
    candidates = sorted(
        path
        for path in (case_dir / "inputs").glob("01_直播回放.*")
        if path.is_file()
    )
    if len(candidates) != 1:
        raise ValueError(f"必须恰好有一个 inputs/01_直播回放.*，当前={len(candidates)}")
    return candidates[0]


def probe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())

    if shutil.which("mdls"):
        result = subprocess.run(
            ["mdls", "-raw", "-name", "kMDItemDurationSeconds", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        value = result.stdout.strip()
        if value not in {"", "(null)", "null"}:
            return float(value)

    afinfo = shutil.which("afinfo")
    if afinfo:
        result = subprocess.run(
            [afinfo, str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        match = re.search(r"estimated duration:\s*([0-9.]+)\s*sec", result.stdout)
        if match:
            return float(match.group(1))
    raise RuntimeError("找不到可用的时长探测工具（ffprobe、mdls 或 afinfo）。")


def verify_manifest(case_dir: Path, manifest: dict[str, object]) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("整场音频manifest顶层必须为对象。")
    source = manifest.get("source", {})
    output = manifest.get("output", {})
    if not isinstance(source, dict) or not isinstance(output, dict):
        raise ValueError("整场音频manifest缺少source或output对象。")
    source_path = case_dir / str(source.get("path", ""))
    output_path = case_dir / str(output.get("path", ""))
    if not source_path.is_file() or not output_path.is_file():
        raise ValueError("整场音频manifest指向的源文件或输出文件不存在。")
    if source.get("sha256") != file_sha256(source_path):
        raise ValueError("原回放SHA-256与整场音频manifest不一致。")
    if output.get("sha256") != file_sha256(output_path):
        raise ValueError("整场音频SHA-256与manifest不一致。")
    source_duration = float(source.get("duration_seconds", 0))
    output_duration = float(output.get("duration_seconds", 0))
    tolerance = max(2.0, source_duration * 0.005)
    if source_duration <= 0 or output_duration <= 0:
        raise ValueError("整场音频manifest缺少有效时长。")
    if abs(source_duration - output_duration) > tolerance:
        raise ValueError(
            f"整场音频时长偏差过大：源={source_duration:.3f}s 输出={output_duration:.3f}s"
        )


def extract_full_audio(case_dir: Path) -> dict[str, object]:
    case_dir = case_dir.resolve()
    replay_path = find_replay(case_dir)
    output_path = case_dir / OUTPUT
    manifest_path = case_dir / MANIFEST

    if output_path.exists() or manifest_path.exists():
        if not output_path.is_file() or not manifest_path.is_file():
            raise FileExistsError("整场音频或manifest只存在一项，拒绝覆盖，请先人工核对。")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        verify_manifest(case_dir, manifest)
        return {**manifest, "reused": True}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        prefix=".整场音频-", suffix=".m4a", dir=output_path.parent, delete=False
    )
    temporary_path = Path(temporary.name)
    temporary.close()
    temporary_path.unlink()

    tool = "ffmpeg aac 128k"

    try:
        audio_clip(replay_path, temporary_path)
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise RuntimeError("音频转换完成但输出文件为空。")
        source_duration = duration(replay_path)
        output_duration = duration(temporary_path)
        tolerance = max(2.0, source_duration * 0.005)
        if abs(source_duration - output_duration) > tolerance:
            raise RuntimeError(
                f"整场音频时长偏差过大：源={source_duration:.3f}s 输出={output_duration:.3f}s"
            )
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    manifest: dict[str, object] = {
        "schema_version": 1,
        "conversion": tool,
        "source": {
            "path": replay_path.relative_to(case_dir).as_posix(),
            "sha256": file_sha256(replay_path),
            "bytes": replay_path.stat().st_size,
            "duration_seconds": round(source_duration, 3),
        },
        "output": {
            "path": OUTPUT.as_posix(),
            "sha256": file_sha256(output_path),
            "bytes": output_path.stat().st_size,
            "duration_seconds": round(output_duration, 3),
        },
    }
    verify_manifest(case_dir, manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**manifest, "reused": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="从单人直播回放分离并核验整场M4A。")
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()
    try:
        result = extract_full_audio(args.case_dir)
    except (
        FileExistsError,
        FileNotFoundError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        parser.exit(1, f"FULL_AUDIO=FAIL\n{exc}\n")
    print(f"FULL_AUDIO=PASS reused={str(result['reused']).lower()}")
    print(f"output={args.case_dir.resolve() / OUTPUT}")
    print(f"manifest={args.case_dir.resolve() / MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
