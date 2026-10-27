#!/usr/bin/env python3
"""Read-only transcript quality signals; never claim a listening or completeness result."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from media_tools import duration
from route_livestream import SPEAKER_HEADER_RE, find_replay
from source_turns import blocks_from_text
from workspace import input_manifest_errors


def inspect_source(text: str, media_seconds: float, cues: list[dict] | None = None) -> dict:
    if not math.isfinite(media_seconds) or media_seconds <= 0:
        raise ValueError('回放时长必须为有效正数')
    headers = list(SPEAKER_HEADER_RE.finditer(text))
    turns = blocks_from_text(text)
    if not turns:
        raise ValueError('原稿没有可识别的发言人时间码')
    payloads = [text[m.end():headers[i + 1].start() if i + 1 < len(headers) else len(text)].strip()
                for i, m in enumerate(headers)]
    normalized = [re.sub(r'[\W_]+', '', value, flags=re.UNICODE) for value in payloads]
    repeated = []
    for i, value in enumerate(normalized):
        if i and len(value) >= 16 and value == normalized[i-1]:
            repeated.append({'kind': '相邻长块文字相同', 'timecode': turns[i]['timecode'],
                             'source_ids': [turns[i-1]['id'], turns[i]['id']]})
        match = re.search(r'(.{12,100})\1', value)
        if match:
            repeated.append({'kind': '同一块内长片段连续重复', 'timecode': turns[i]['timecode'],
                             'source_ids': [turns[i]['id']]})
    samples = []
    for name, point in [('开头', 0), ('中段', media_seconds / 2), ('结尾', media_seconds)]:
        nearest = min(turns, key=lambda row: abs(row['start_seconds']-point))
        samples.append({'position': name, 'media_seconds': round(point, 3),
                        'nearest_source_timecode': nearest['timecode'], 'source_id': nearest['id']})
    gaps = None
    if cues is not None:
        intervals = []
        for cue in cues:
            start, end = cue['start_ms']/1000, cue['end_ms']/1000
            if not all(math.isfinite(x) for x in (start, end)) or start < 0 or end <= start or end > media_seconds + 2:
                raise ValueError('字幕毫秒记录无效或超出回放')
            intervals.append((start, min(end, media_seconds)))
        if not intervals or any(b[0] < a[0] for a, b in zip(intervals, intervals[1:])):
            raise ValueError('字幕毫秒记录为空或起点倒序')
        gaps, cursor = [], 0.0
        for start, end in intervals:
            if start-cursor >= 30:
                gaps.append({'start_seconds':round(cursor, 3), 'end_seconds':round(start, 3)})
            cursor = max(cursor, end)
        if media_seconds-cursor >= 30:
            gaps.append({'start_seconds':round(cursor, 3), 'end_seconds':round(media_seconds, 3)})
    return {'status':'仅供核对的线索，未完成回听', 'media_duration_seconds':media_seconds,
            'source_turn_count':len(turns), 'source_first_start':turns[0]['timecode'],
            'source_last_start':turns[-1]['timecode'], 'samples':samples,
            'timed_gap_candidates':None if gaps is None else gaps[:50],
            'timed_gap_candidate_count':None if gaps is None else len(gaps),
            'gap_note':('字幕区间以外的片段仅是候选，可能是静音、音乐或字幕时间偏移，必须对照原声。' if cues is not None
                        else '普通原稿只有发言起点，不能从相邻起点或末段起点推断缺稿、静音或尾部结束时间。'),
            'repeated_text_candidates':repeated[:50], 'repeated_candidate_count':len(repeated),
            'review_note':'核对实际发言语言及首、中、尾内容；留意静音或音乐附近的重复幻觉。任何候选都可能是正常现象，不自动删字、改时间或当作已经听清。'}


def main() -> int:
    parser = argparse.ArgumentParser(description='输入原稿质量线索，仅读取，不修改原文件或替代回听')
    parser.add_argument('case', type=Path, help='已初始化的场次目录')
    args = parser.parse_args(); case = args.case.expanduser().resolve()
    try:
        path = case/'inputs/02_带时间码原稿.md'; raw = path.read_bytes()
        text = raw.decode('utf-8-sig'); length = duration(find_replay(case))
        subtitle = case/'_work/字幕导入记录.json'; cues = None
        if subtitle.is_file():
            errors = input_manifest_errors(case)
            if errors:
                raise ValueError('字幕来源绑定检查失败：' + '；'.join(errors))
            cues = json.loads(subtitle.read_text(encoding='utf-8-sig'))['cues']
        result = inspect_source(text, length, cues)
        result['source_sha256'] = hashlib.sha256(raw).hexdigest()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({'status':'无法检查','reason':str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
