#!/usr/bin/env python3
"""Record an evidence-backed solo/dual routing decision for a livestream case."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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
POSITIONS = ['开场', '25%', '50%', '75%', '结尾']


def validate_observations(case_dir: Path, observations: object) -> list[dict]:
    """Validate recorded coverage of real probe windows, not whether listening occurred."""
    if not isinstance(observations, list) or len(observations) != 5 or not all(isinstance(x, dict) for x in observations):
        raise ValueError('需要五条结构化核验记录，分别对应开场、25%、50%、75%、结尾。')
    if {item.get('position') for item in observations} != set(POSITIONS):
        raise ValueError('五条核验记录必须覆盖五个不同的位置，不能只重复开场。')
    folder = case_dir/'_work/分流试听'
    manifest_path = folder/'manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        if manifest.get('source_sha256') != file_sha256(find_replay(case_dir)):
            raise ValueError('分流试听未绑定当前回放，请核对并重新生成。')
        length = float(manifest.get('source_duration_seconds', 0))
        if not math.isfinite(length) or length <= 0:
            raise ValueError('试听记录缺少有效回放时长；旧试听包需按实际素材重新生成。')
        clips = manifest.get('clips')
        if not isinstance(clips, list) or len(clips) != 5 or {c.get('position') for c in clips} != set(POSITIONS):
            raise ValueError('试听清单没有完整的五个位置。')
        if len({c['file'] for c in clips}) != 5:
            raise ValueError('素材不足五个独立试听窗口；不要复制片段或补造记录。')
        if len({(float(c['start_seconds']), float(c['duration_seconds'])) for c in clips}) != 5:
            raise ValueError('素材不足五个不同时间窗口；不同文件名不能替代独立位置。')
        indexed = {c['position']:c for c in clips}
        records = {item['position']:item for item in observations}
        validated = []
        for position,fraction in zip(POSITIONS,[0,.25,.5,.75,1]):
            clip, record = indexed[position], records[position]
            expected_start = min(max(0, length-20),length*fraction)
            expected_duration = min(20,length-expected_start)
            start, span = float(clip['start_seconds']), float(clip['duration_seconds'])
            if not all(math.isfinite(x) for x in [start,span]) or abs(start-expected_start)>.01 or abs(span-expected_duration)>.01:
                raise ValueError(f'{position} 的试听窗口不对应回放实际位置。')
            name = str(clip['file']); path=folder/name
            if Path(name).name != name or path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()) or not path.is_file() or clip.get('audio_sha256') != file_sha256(path):
                raise ValueError(f'{position} 的试听文件缺失、越界或已变化。')
            observed_start, observed_end = float(record['start_seconds']),float(record['end_seconds'])
            if not all(math.isfinite(x) for x in [observed_start,observed_end]) or abs(observed_start-start)>.01 or abs(observed_end-start-span)>.01:
                raise ValueError(f'{position} 的核验时间没有对应试听窗口。')
            method, finding = record.get('method'), record.get('finding')
            if not isinstance(method,str) or not re.search(r'真人回听|音频理解|回放画面',method) or re.search(r'待填|待确认|原稿标签|转写文本|仅凭上下文|尚未|未进行|未完成|没有(?:进行|完成)|计划|准备|未回听|未核验|未执行',method):
                raise ValueError(f'{position} 需要实际的回听、音频理解或回放画面核验方式。')
            if not isinstance(finding,str) or not finding.strip() or re.fullmatch(r'待填(?:写)?|待确认|未知|不确定|填写实际发现',finding.strip()) or re.search(r'听不清|无法(?:判断|确认|确定|辨认|识别)|不能(?:判断|确认|确定|辨认|识别)|人数(?:未知|不确定|待确认)|未(?:能)?确认.*(?:人数|说话人)|uncertain',finding,re.I):
                raise ValueError(f'{position} 缺少实际发现；无法确认时请记录uncertain，不猜人数。')
            validated.append({'position':position,'start_seconds':start,'end_seconds':start+span,'method':method.strip(),'finding':finding.strip()})
        return validated
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError(f'分流试听或结构化记录缺失/无效：{exc}') from exc


def observation_texts(records: list[dict]) -> list[str]:
    return [f"{r['position']}｜{r['start_seconds']:g}–{r['end_seconds']:g}秒｜{r['method']}｜{r['finding']}" for r in records]


def route_position_errors(case: Path, route: dict, *, handoff: bool = False) -> list[str]:
    if route.get('schema_version') != 2:
        return ['旧分流记录缺少五位置结构化依据；正式交接前请按真实回放补核并保留旧记录。'] if handoff else []
    try:
        records=validate_observations(case,route.get('replay',{}).get('observations'))
        if route['replay'].get('probe_manifest_sha256') != file_sha256(case/'_work/分流试听/manifest.json'):
            raise ValueError('分流所绑定的试听清单已变化。')
        if route['replay'].get('evidence') != observation_texts(records):
            raise ValueError('分流文字与五位置核验记录不一致。')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [str(exc)]
    return []


def valid_default_map(mapping: object, speaker_ids: set[str], labels: set[str] | None = None) -> bool:
    if not isinstance(mapping, dict) or not mapping or (labels is not None and set(mapping) != labels):
        return False
    if '发言人未标注' not in mapping:
        return set(str(value) for value in mapping.values()) == speaker_ids
    # Unknown subtitle labels must never acquire a guessed default identity.
    return mapping['发言人未标注'] is None and all(
        value is None if label == '发言人未标注' else isinstance(value, str) and value in speaker_ids
        for label, value in mapping.items())


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
    observations: list[dict] | None = None,
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

    validated = []
    if acoustic_speakers in {"1", "2"}:
        if observations is None:
            raise ValueError('单人/双人需要五位置结构化核验记录；先运行probe，再使用 --observations，不能用五条不同文字代替。')
        validated=validate_observations(case_dir,observations)
        evidence=observation_texts(validated)
    if acoustic_speakers in {"3+", "uncertain"} and not evidence:
        raise ValueError("阻断分流也必须记录至少一条回放证据。")

    warnings: list[str] = []
    if '发言人未标注' in labels:
        warnings.append('字幕未提供说话人标签；未标注不代表同一人，按真实回放重建归属。')
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

    result = {
        "schema_version": 2 if validated else 1,
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
    if validated:
        result['replay']['observations']=validated
        result['replay']['probe_manifest_sha256']=file_sha256(case_dir/'_work/分流试听/manifest.json')
    return result


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
        help="仅用于3+/uncertain的阻断说明，可重复。正常分流用--observations。",
    )
    parser.add_argument('--observations',type=Path,help='五位置结构化核验JSON；Agent按probe实际片段填写，不能代填未发生的回听。')
    args = parser.parse_args()
    try:
        route = build_route(
            args.case_dir,
            acoustic_speakers=args.acoustic_speakers,
            evidence=args.evidence,
            observations=json.loads(args.observations.read_text(encoding='utf-8-sig')) if args.observations else None,
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
