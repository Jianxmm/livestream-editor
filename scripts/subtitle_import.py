"""Conservative subtitle import. Original bytes and exact cue times remain available."""
from __future__ import annotations

import html
import re


STAMP = r'(?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3}'
TIMING = re.compile(rf'^({STAMP})[ \t]+-->[ \t]+({STAMP})(?:[ \t]+(.*))?$')
VOICE = re.compile(r'<v(?:\.[^\s>]*)?\s+([^>]+)>')
INLINE_TIME = re.compile(rf'<{STAMP}>')
STYLE = re.compile(r'</?(?:b|i|u|c(?:\.[^\s>]*)?|lang(?:\s+[^>]+)?|font(?:\s+[^>]+)?|v)\s*>', re.I)


def milliseconds(value: str, kind: str) -> int:
    expected = ',' if kind == 'srt' else '.'
    if expected not in value:
        raise ValueError(f'{kind.upper()} 时间码的小数分隔符应为 {expected}：{value}')
    whole, fraction = value.split(expected)
    parts = [int(x) for x in whole.split(':')]
    if kind == 'srt' and len(parts) != 3:
        raise ValueError('SRT 需要 时:分:秒,毫秒 格式')
    if parts[-1] >= 60 or parts[-2] >= 60 or (len(parts) == 3 and parts[0] > 99):
        raise ValueError(f'时间码超出支持范围：{value}')
    seconds = sum(n * 60 ** i for i, n in enumerate(reversed(parts)))
    return seconds * 1000 + int(fraction)


def timecode(value: int) -> str:
    seconds = value // 1000
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


def convert_subtitles(data: bytes, kind: str) -> tuple[str, dict]:
    if kind not in {'srt', 'vtt'}:
        raise ValueError('只支持 SRT 或 VTT 字幕导入')
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    try:
        text = data.decode(encoding).replace('\r\n', '\n').replace('\r', '\n').strip()
    except UnicodeError as exc:
        raise ValueError('字幕编码无法识别，请从原工具另存 UTF-8 副本，保留原文件。') from exc
    if not text:
        raise ValueError('字幕文件为空')
    chunks = re.split(r'\n(?:[ \t]*\n)+', text)
    metadata = []
    if kind == 'vtt':
        header = chunks.pop(0).splitlines()
        if not header or not re.fullmatch(r'WEBVTT(?:[ \t].*)?', header[0]):
            raise ValueError('VTT 缺少 WEBVTT 文件头')
        if any('X-TIMESTAMP-MAP' in line.upper() or '-->' in line for line in header):
            raise ValueError('VTT 含分段时间映射或文件头缺少空行；请导出与完整回放对齐的字幕。')
        metadata.append('\n'.join(header))
    cues, voices, normalized = [], {}, []
    for block in chunks:
        lines = block.splitlines()
        if kind == 'vtt' and (lines[0] in {'STYLE', 'REGION', 'NOTE'} or lines[0].startswith(('NOTE ', 'NOTE\t'))):
            metadata.append(block)
            continue
        offset = 0 if TIMING.fullmatch(lines[0]) else 1
        identifier = '' if offset == 0 else lines[0]
        if kind == 'srt' and (offset != 1 or not identifier.isdecimal()):
            raise ValueError(f'SRT 字幕缺少数字序号：{lines[0][:80]}')
        if len(lines) <= offset or not (match := TIMING.fullmatch(lines[offset])):
            raise ValueError(f'无法识别字幕时间行：{lines[0][:80]}')
        start, end = (milliseconds(match.group(i), kind) for i in (1, 2))
        number = len(cues) + 1
        if end <= start:
            raise ValueError(f'第 {number} 条字幕结束时间必须晚于开始时间')
        if cues and start < cues[-1]['end_ms']:
            raise ValueError(f'第 {number} 条字幕重叠或倒序，可能是滚动字幕；请导出不重叠的完整转写稿。')
        if cues and start // 1000 <= cues[-1]['start_ms'] // 1000:
            raise ValueError(f'第 {number} 条字幕与前条落在同一秒；当前原稿索引精确到秒，请另导出较长的字幕段。不会移动时间或合并原话。')
        raw = '\n'.join(lines[offset + 1:])
        if not raw.strip():
            raise ValueError(f'第 {number} 条字幕没有文字')
        voice_matches = list(VOICE.finditer(raw)) if kind == 'vtt' else []
        if len(voice_matches) > 1 or (voice_matches and raw[:voice_matches[0].start()].strip()):
            raise ValueError(f'第 {number} 条字幕在同一时间块混有多个声音或无标签文字，请先从原工具分开导出。')
        if voice_matches and '</v>' in raw and raw.split('</v>', 1)[1].strip():
            raise ValueError(f'第 {number} 条字幕的声音标签后还有未标注文字，请分开导出。')
        voice = html.unescape(voice_matches[0].group(1)).strip() if voice_matches else None
        if voice is not None and not voice:
            raise ValueError(f'第 {number} 条字幕的声音标签为空')
        if voice is not None:
            voices.setdefault(voice, f'发言人{len(voices) + 1}')
        label = voices[voice] if voice is not None else '发言人未标注'
        if re.search(r'</?(?:ruby|rt)(?:\s|>)', raw, re.I):
            raise ValueError(f'第 {number} 条字幕含注音，请另导出纯文字，避免把注音当成原话。')
        # Remove only recognized presentation markup; preserve unknown/literal text.
        clean = VOICE.sub('', raw) if kind == 'vtt' else raw
        clean = html.unescape(STYLE.sub('', INLINE_TIME.sub('', clean))).strip()
        if not clean:
            raise ValueError(f'第 {number} 条字幕没有可导入的文字')
        # A payload must not be able to inject a new transcript header.
        if re.search(r'^发言人\S*\s+(?:\d{1,2}:)?\d{2}:\d{2}\s*$', clean, re.M):
            raise ValueError(f'第 {number} 条字幕正文疑似含另一份转写时间行，请核对原始导出。')
        stamp = timecode(start)
        cues.append({'number': number, 'identifier': identifier, 'start_ms': start, 'end_ms': end,
                     'settings': match.group(3) or '', 'original_voice': voice,
                     'label': label, 'normalized_time': stamp, 'raw_text': raw, 'text': clean})
        normalized.append(f'{label}   {stamp}\n{clean}\n')
    if not cues:
        raise ValueError('字幕没有可导入的文字条目')
    result = {'version': 1, 'format': kind, 'encoding': encoding, 'cue_count': len(cues),
              'speaker_status': '未核验；字幕标签不证明真实身份或说话人数',
              'voice_labels': voices, 'metadata_blocks': metadata, 'cues': cues,
              'time_policy': '原始毫秒时间完整保留；规范副本起点向下取整到秒，误差小于1秒；不合并、不删字、不去重'}
    preamble = '<!-- 字幕导入副本：说话人未核验；原始文件及毫秒时间见输入与字幕导入记录。 -->\n\n'
    return preamble + '\n'.join(normalized), result
