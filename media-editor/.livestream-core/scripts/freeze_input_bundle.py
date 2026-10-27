#!/usr/bin/env python3
"""Freeze every file supplied to a dual-track case as a hash-bound input bundle."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from route_livestream import find_replay


MANIFEST = "_work/双轨输入清单.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_input_bundle(case_dir: Path) -> dict[str, object]:
    case_dir = case_dir.resolve()
    inputs_dir = case_dir / "inputs"
    if not inputs_dir.is_dir():
        raise FileNotFoundError(f"缺少inputs目录：{inputs_dir}")
    files: list[dict[str, object]] = []
    for path in sorted(inputs_dir.rglob("*")):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        files.append(
            {
                "path": path.relative_to(case_dir).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    replay = find_replay(case_dir)
    required = {replay.relative_to(case_dir).as_posix(), "inputs/02_带时间码原稿.md"}
    present = {str(item["path"]) for item in files}
    if not required.issubset(present):
        raise FileNotFoundError("双轨验证必须包含标准回放和带时间码原稿。")
    canonical = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "case": case_dir.name,
        "files": files,
        "bundle_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def freeze_input_bundle(case_dir: Path) -> dict[str, object]:
    case_dir = case_dir.resolve()
    current = inspect_input_bundle(case_dir)
    manifest_path = case_dir / MANIFEST
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if (
            existing.get("case") == current["case"]
            and existing.get("files") == current["files"]
            and existing.get("bundle_sha256") == current["bundle_sha256"]
        ):
            return existing
        raise FileExistsError("双轨输入清单已存在且inputs已变化；不得静默覆盖验证输入。")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        **current,
    }
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def verify_input_bundle(case_dir: Path) -> dict[str, object]:
    case_dir = case_dir.resolve()
    manifest_path = case_dir / MANIFEST
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺双轨输入清单：{manifest_path}")
    frozen = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    current = inspect_input_bundle(case_dir)
    if (
        frozen.get("case") != current["case"]
        or frozen.get("files") != current["files"]
        or frozen.get("bundle_sha256") != current["bundle_sha256"]
    ):
        raise ValueError("当前inputs与已冻结的双轨输入清单不一致。")
    return frozen


def main() -> int:
    parser = argparse.ArgumentParser(description="冻结或核验双轨测试的完整输入包。")
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = (
        verify_input_bundle(args.case_dir)
        if args.verify
        else freeze_input_bundle(args.case_dir)
    )
    print(
        f"INPUT_BUNDLE={'VERIFIED' if args.verify else 'FROZEN'} "
        f"case={result['case']} files={len(result['files'])} sha256={result['bundle_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
