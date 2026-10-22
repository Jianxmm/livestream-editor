#!/usr/bin/env python3
"""Record an evidence-backed solo/dual routing decision for a livestream case."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


SOURCE = Path("inputs/02_带时间码原稿.md")
OUTPUT = Path("_work/场次分流.json")
SPEAKER_HEADER_RE = re.compile(
    r"^(?P<label>(?:发言人|说话人)\s*[^\s]+|Speaker\s*[^\s]+)"
    r"\s+(?P<time>(?:\d{1,2}:)?\d{2}:\d{2})\s*$",
    re.MULTILINE | re.IGNORECASE,
)
VALID_COUNTS = {"1", "2", "3+", "uncertain"}


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


def normalize_label(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def build_route(
    case_dir: Path,
    *,
    acoustic_speakers: str,
    evidence: list[str],
) -> dict[str, object]:
    case_dir = case_dir.resolve()
    if acoustic_speakers not in VALID_COUNTS:
        raise ValueError(f"无效声学人数：{acoustic_speakers}")
    source_path = case_dir / SOURCE
    if not source_path.is_file():
        raise FileNotFoundError(f"缺少原稿：{source_path}")
    replay_path = find_replay(case_dir)
    source_text = source_path.read_text(encoding="utf-8-sig")
    matches = list(SPEAKER_HEADER_RE.finditer(source_text))
    labels = sorted({normalize_label(match.group("label")) for match in matches})

    if acoustic_speakers in {"1", "2"} and not matches:
        raise ValueError("原稿没有可识别的发言人时间块；先保留原稿并转换格式，不能继续正文整理。")

    if acoustic_speakers in {"1", "2"} and len({item.strip() for item in evidence if item.strip()}) < 5:
        raise ValueError("单人/双人分流至少需要开场、25%、50%、75%和结尾五条回放证据。")
    if acoustic_speakers in {"3+", "uncertain"} and not evidence:
        raise ValueError("阻断分流也必须记录至少一条回放证据。")

    warnings: list[str] = []
    if acoustic_speakers == "1" and len(labels) > 1:
        warnings.append("原稿标签多于一个，但回放确认只有一个内容说话人；标签不得覆盖声学结论。")
    if acoustic_speakers == "2" and len(labels) < 2:
        warnings.append("原稿标签少于两个，但回放确认有两个内容说话人；双人流程须重建说话人时间轴。")

    if acoustic_speakers == "1":
        mode, status, target = "solo", "PASS", "references/solo.md"
    elif acoustic_speakers == "2":
        mode, status, target = "dual", "PASS", "references/dual.md"
    else:
        mode, status, target = "unsupported", "BLOCKED", None

    return {
        "schema_version": 1,
        "status": status,
        "mode": mode,
        "acoustic_speaker_count": acoustic_speakers,
        "decision_basis": "直播回放中的内容说话人；原转写标签只作冲突线索",
        "target_skill": target,
        "source": {
            "path": SOURCE.as_posix(),
            "sha256": file_sha256(source_path),
            "speaker_labels": labels,
            "speaker_label_count": len(labels),
            "timecoded_blocks": len(matches),
        },
        "replay": {
            "path": replay_path.relative_to(case_dir).as_posix(),
            "sha256": file_sha256(replay_path),
            "evidence": evidence,
        },
        "warnings": warnings,
    }


def write_route(case_dir: Path, route: dict[str, object]) -> Path:
    output_path = case_dir.resolve() / OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(route, ensure_ascii=False, indent=2) + "\n"
    if output_path.exists():
        if output_path.read_text(encoding="utf-8-sig") == rendered:
            return output_path
        raise FileExistsError(f"已有不同分流记录，拒绝覆盖：{output_path}")
    output_path.write_text(rendered, encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="根据回放证据记录单人/双人分流。")
    parser.add_argument("case_dir", type=Path)
    parser.add_argument(
        "--acoustic-speakers",
        required=True,
        choices=sorted(VALID_COUNTS),
        help="回放确认的内容说话人数；不确定时使用 uncertain。",
    )
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="回放核验记录，至少包含时间范围和判断；可重复。",
    )
    args = parser.parse_args()
    try:
        route = build_route(
            args.case_dir,
            acoustic_speakers=args.acoustic_speakers,
            evidence=args.evidence,
        )
        output = write_route(args.case_dir, route)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        parser.exit(1, f"ROUTE=FAIL\n{exc}\n")

    print(f"ROUTE={route['status']}")
    print(f"mode={route['mode']}")
    print(f"target_skill={route['target_skill'] or 'none'}")
    print(f"output={output}")
    return 0 if route["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
