#!/usr/bin/env python3
"""Link each review issue to an immutable excerpt from the source transcript."""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from source_turns import blocks_from_text,contract_state


SOURCE = Path("inputs/02_带时间码原稿.md")
ISSUES = Path("outputs/02_待核问题清单.md")
OUTPUT = Path("_work/待核原文索引.md")
ISSUE_ROW_RE = re.compile(r"^\|\s*(P[012])\s*\|")
TIMESTAMP_RE = re.compile(r"(?<!\d)(?:\d{1,2}:)?\d{2}:\d{2}(?!\d)")
SOURCE_HEADER_RE = re.compile(
    r"^(?P<label>(?:发言人|说话人)\s*[^\s]+|Speaker\s*[^\s]+)"
    r"\s+(?P<time>(?:\d{1,2}:)?\d{2}:\d{2})\s*$",
    re.MULTILINE | re.IGNORECASE,
)
EXISTING_LINK_RE = re.compile(
    r"^\[(?P<label>.+)\]\(\.\./_work/待核原文索引\.md#review-\d{3}\)$"
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def to_seconds(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"无效时间码：{value}")


@dataclass(frozen=True)
class SourceBlock:
    label: str
    timecode: str
    start: int
    end: int
    text: str
    block_id: str = ""


@dataclass(frozen=True)
class Issue:
    index: int
    priority: str
    timecode: str
    issue_type: str
    text: str
    action: str
    status: str


def parse_source_blocks(text: str, *, ordered: bool = False) -> list[SourceBlock]:
    matches = list(SOURCE_HEADER_RE.finditer(text))
    blocks: list[SourceBlock] = []
    identities=blocks_from_text(text) if ordered else None
    for index, match in enumerate(matches):
        next_start = (
            to_seconds(matches[index + 1].group("time"))
            if index + 1 < len(matches)
            else to_seconds(match.group("time")) + 90
        )
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        start = to_seconds(match.group("time"))
        if ordered:
            next_start=next((to_seconds(n.group('time')) for n in matches[index+1:] if to_seconds(n.group('time'))>start),start+90)
        blocks.append(
            SourceBlock(
                label=match.group("label"),
                timecode=match.group("time"),
                start=start,
                end=max(start, next_start - 1),
                text=text[match.end() : body_end].strip(),
                block_id=identities[index]["id"] if identities else "",
            )
        )
    return blocks


def raw_timecode(value: str) -> str:
    match = EXISTING_LINK_RE.match(value.strip())
    return match.group("label") if match else value.strip()


def link_issue_timecodes(text: str) -> tuple[str, list[Issue]]:
    output_lines: list[str] = []
    issues: list[Issue] = []
    for line in text.splitlines():
        row = ISSUE_ROW_RE.match(line)
        if not row:
            output_lines.append(line)
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6:
            raise ValueError(f"待核问题行少于6列：{line}")
        index = len(issues) + 1
        timecode = raw_timecode(cells[1])
        if not TIMESTAMP_RE.search(timecode):
            if cells[0] == "P2" or cells[-1] != "待确认":
                output_lines.append(line)
                continue
            raise ValueError(f"待核问题缺少可定位时间码：{line}")
        cells[1] = f"[{timecode}](../_work/待核原文索引.md#review-{index:03d})"
        output_lines.append("| " + " | ".join(cells) + " |")
        issues.append(
            Issue(
                index=index,
                priority=cells[0],
                timecode=timecode,
                issue_type=cells[2],
                text=cells[3],
                action=cells[4],
                status=cells[-1],
            )
        )
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(output_lines) + suffix, issues


def matching_block_indexes(issue: Issue, blocks: list[SourceBlock]) -> list[int]:
    timestamps = [to_seconds(value) for value in TIMESTAMP_RE.findall(issue.timecode)]
    ranges: list[tuple[int, int]] = []
    for index in range(0, len(timestamps), 2):
        start = timestamps[index]
        end = timestamps[index + 1] if index + 1 < len(timestamps) else start
        ranges.append((min(start, end), max(start, end)))
    hits = {
        index
        for index, block in enumerate(blocks)
        for start, end in ranges
        if block.start <= end and block.end >= start
    }
    if not hits and blocks:
        target = timestamps[0]
        hits.add(min(range(len(blocks)), key=lambda index: abs(blocks[index].start - target)))
    with_context = set(hits)
    for index in hits:
        if index > 0:
            with_context.add(index - 1)
        if index + 1 < len(blocks):
            with_context.add(index + 1)
    return sorted(with_context)


def safe_fence(text: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def render_index(
    *,
    source_path: Path,
    source_sha256: str,
    issues_path: Path,
    issues_sha256: str,
    blocks: list[SourceBlock],
    issues: list[Issue],
) -> str:
    lines = [
        "# 待核原文索引",
        "",
        f"<!-- source: {source_path.as_posix()} -->",
        f"<!-- source_sha256: {source_sha256} -->",
        f"<!-- issues: {issues_path.as_posix()} -->",
        f"<!-- issues_sha256: {issues_sha256} -->",
        "",
        "> 点击《待核问题清单》中的时间码可到达本页对应位置。以下只复制原稿命中块和相邻上下文，不改写原文；最终判断仍以真人回听为准。",
        "",
    ]
    if not issues:
        lines.extend(["当前没有待核问题。", ""])
        return "\n".join(lines)

    emitted_anchors=set()
    for issue in issues:
        selected = [blocks[index] for index in matching_block_indexes(issue, blocks)]
        excerpt = "\n\n".join(
            f"{block.label}   {block.timecode}\n{block.text}" for block in selected
        )
        fence = safe_fence(excerpt)
        excerpt_lines=[fence,excerpt,fence]
        if any(block.block_id for block in selected):
            excerpt_lines=[]
            for block in selected:
                if block.block_id not in emitted_anchors:
                    excerpt_lines.extend([f'<a id="source-{block.block_id}"></a>',''])
                    emitted_anchors.add(block.block_id)
                value=f"{block.label}   {block.timecode}\n{block.text}"
                bound=safe_fence(value)
                excerpt_lines.extend([f'原稿块 `{block.block_id}`（原时间 {block.timecode}）', '',bound,value,bound,''])
        lines.extend(
            [
                f'<a id="review-{issue.index:03d}"></a>',
                "",
                f"## {issue.priority}-{issue.index:03d}｜{issue.issue_type}",
                "",
                f"- 待核时间码：{issue.timecode}",
                f"- 问题：{issue.text}",
                f"- 处理建议：{issue.action}",
                f"- 当前状态：{issue.status}",
                f"- [返回待核问题清单](../outputs/{ISSUES.name})",
                "",
                "### 原稿命中块与相邻上下文",
                "",
                *excerpt_lines,
                "",
            ]
        )
    return "\n".join(lines)


def build_source_review_index(case_dir: Path) -> dict[str, object]:
    case_dir = case_dir.resolve()
    source_path = case_dir / SOURCE
    issues_path = case_dir / ISSUES
    output_path = case_dir / OUTPUT
    if not source_path.is_file():
        raise FileNotFoundError(f"缺少原稿：{source_path}")
    if not issues_path.is_file():
        raise FileNotFoundError(f"缺少待核清单：{issues_path}")

    source_text = source_path.read_text(encoding="utf-8-sig")
    ordered,errors=contract_state(case_dir)
    if errors:raise ValueError("；".join(errors))
    blocks = parse_source_blocks(source_text,ordered=ordered)
    if not blocks:
        raise ValueError("原稿没有识别到带说话人和时间码的发言块。")
    linked_issue_text, issues = link_issue_timecodes(
        issues_path.read_text(encoding="utf-8-sig")
    )
    issues_path.write_text(linked_issue_text, encoding="utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_index(
        source_path=SOURCE,
        source_sha256=file_sha256(source_path),
        issues_path=ISSUES,
        issues_sha256=file_sha256(issues_path),
        blocks=blocks,
        issues=issues,
    )
    output_path.write_text(rendered, encoding="utf-8")
    return {
        "issues": len(issues),
        "source_blocks": len(blocks),
        "output": str(output_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成待核问题到原稿摘录的可点击索引。")
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()
    try:
        result = build_source_review_index(args.case_dir)
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(1, f"SOURCE_INDEX=FAIL\n{exc}\n")
    print(
        f"SOURCE_INDEX=PASS issues={result['issues']} "
        f"source_blocks={result['source_blocks']} output={result['output']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
