#!/usr/bin/env python3
"""Build a clickable, transcript-assisted index for human audio review."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_review_index(case_dir: Path) -> dict[str, object]:
    case_dir = case_dir.resolve()
    clip_dir = case_dir / "_work/review_clips"
    manifest_path = clip_dir / "manifest.json"
    transcript_path = clip_dir / "machine_transcripts.json"
    output_path = case_dir / "_work/回听辅助索引.md"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺少回听manifest：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    clips = manifest.get("clips", [])
    samples = manifest.get("samples", [])
    if not isinstance(clips, list) or not isinstance(samples, list):
        raise ValueError("回听manifest必须包含clips和samples数组。")

    transcript_map: dict[str, str] = {}
    machine_sha = "none"
    if transcript_path.is_file():
        transcripts = json.loads(transcript_path.read_text(encoding="utf-8-sig"))
        if transcripts.get("manifest_sha256") != file_sha256(manifest_path):
            raise ValueError("机器辅助转写与当前回听manifest不一致。")
        machine_sha = file_sha256(transcript_path)
        transcript_map = {
            str(item.get("filename")): str(item.get("text", ""))
            for item in transcripts.get("clips", [])
            if isinstance(item, dict) and item.get("filename")
        }

    lines = [
        "# 回听辅助索引",
        "",
        f"<!-- manifest_sha256: {file_sha256(manifest_path)} -->",
        f"<!-- machine_transcripts_sha256: {machine_sha} -->",
        "",
        "> 本文件只用于快速播放和定位。机器文字可能误听，最终原话、处理结论和终审签字仍统一填写在《回听终审表》里。",
        "",
    ]
    grouped: dict[int, list[dict[str, object]]] = {}
    for item in clips:
        if isinstance(item, dict) and isinstance(item.get("issue_index"), int):
            grouped.setdefault(int(item["issue_index"]), []).append(item)
    for issue_index in sorted(grouped):
        items = grouped[issue_index]
        first = items[0]
        lines.extend(
            [
                f"## P1-{issue_index:02d}｜{first.get('issue_type', '')}",
                "",
                f"- 时间码：{first.get('timecode', '')}",
                f"- 核验内容：{first.get('issue_text', '')}",
                "",
            ]
        )
        seen: set[str] = set()
        for item in items:
            filename = str(item.get("filename", ""))
            if not filename or filename in seen:
                continue
            seen.add(filename)
            relative = f"review_clips/{filename}"
            transcript = transcript_map.get(filename, "未生成机器辅助转写。")
            escaped = html.escape(transcript).replace("\n", "<br>")
            lines.extend(
                [
                    f"### {filename}",
                    "",
                    f"[▶ 单独打开音频]({relative})",
                    "",
                    f'<audio controls preload="none" src="{relative}"></audio>',
                    "",
                    "<details><summary>查看机器辅助转写（不能据此关闭P1）</summary>",
                    "",
                    escaped,
                    "",
                    "</details>",
                    "",
                ]
            )

    lines.extend(["## 随机抽听", ""])
    for item in samples:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename", ""))
        if not filename:
            continue
        relative = f"review_clips/{filename}"
        transcript = transcript_map.get(filename, "未生成机器辅助转写。")
        escaped = html.escape(transcript).replace("\n", "<br>")
        lines.extend(
            [
                f"### {item.get('timecode', '')}｜{filename}",
                "",
                f"[▶ 单独打开音频]({relative})",
                "",
                f'<audio controls preload="none" src="{relative}"></audio>',
                "",
                "<details><summary>查看机器辅助转写（不能据此替代随机抽听）</summary>",
                "",
                escaped,
                "",
                "</details>",
                "",
            ]
        )
    output_path.write_text("\n".join(lines), encoding="utf-8")
    filenames = {
        str(item.get("filename"))
        for item in clips + samples
        if isinstance(item, dict) and item.get("filename")
    }
    return {
        "case": case_dir.name,
        "output": str(output_path),
        "files": len(filenames),
        "machine_transcripts": len(transcript_map),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成可点击播放的回听辅助索引。")
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()
    result = build_review_index(args.case_dir)
    print(
        f"REVIEW_INDEX=PASS files={result['files']} "
        f"machine_transcripts={result['machine_transcripts']} output={result['output']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
