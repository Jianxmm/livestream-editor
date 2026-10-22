#!/usr/bin/env python3
"""Pre-register every case in a multi-case AI versus human validation campaign."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

from freeze_input_bundle import freeze_input_bundle
from route_livestream import file_sha256

SOURCE = "inputs/02_带时间码原稿.md"
MANIFEST = "validation_campaign_manifest.json"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def register_campaign(
    case_dirs: list[Path], output_dir: Path, campaign_name: str
) -> dict[str, object]:
    if len(case_dirs) < 3:
        raise ValueError("稳定质量验证至少预注册3场。")
    if not campaign_name.strip():
        raise ValueError("必须填写验证批次名称。")
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"预注册目录已存在，不允许覆盖：{output_dir}")

    samples: list[dict[str, str]] = []
    seen_cases: set[str] = set()
    seen_hashes: set[str] = set()
    for raw_case in case_dirs:
        case_dir = raw_case.resolve()
        source_path = case_dir / SOURCE
        if not source_path.is_file():
            raise FileNotFoundError(f"缺少带时间码原稿：{source_path}")
        source_hash = file_sha256(source_path)
        content_hash = sha256_text(source_path.read_text(encoding="utf-8-sig"))
        if case_dir.name in seen_cases or content_hash in seen_hashes:
            raise ValueError(f"预注册场次或原稿重复：{case_dir.name}")
        input_bundle = freeze_input_bundle(case_dir)
        seen_cases.add(case_dir.name)
        seen_hashes.add(content_hash)
        samples.append(
            {
                "case": case_dir.name,
                "case_dir": str(case_dir),
                "source": str(source_path),
                "source_sha256": source_hash,
                "input_bundle_sha256": str(input_bundle["bundle_sha256"]),
            }
        )

    manifest: dict[str, object] = {
        "version": 1,
        "campaign_name": campaign_name.strip(),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_count": len(samples),
        "samples": samples,
        "required_experiment_type": "双轨对比",
        "required_reference_exposure": "未在生成阶段使用对照稿",
        "rule": "最终汇总必须包含本manifest中的全部样本，不得事后替换、遗漏或增补场次。",
    }
    output_dir.mkdir(parents=True)
    (output_dir / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="在看到双轨结果前预注册全部验证场次。")
    parser.add_argument("case_dirs", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--campaign-name", required=True)
    args = parser.parse_args()
    manifest = register_campaign(args.case_dirs, args.output_dir, args.campaign_name)
    print(
        f"VALIDATION_CAMPAIGN_REGISTERED=PASS name={manifest['campaign_name']} "
        f"samples={manifest['sample_count']} destination={args.output_dir.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
