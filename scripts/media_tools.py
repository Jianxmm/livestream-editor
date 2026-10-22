"""Portable media operations; subprocess arguments never pass through a shell."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path


def executable(name: str) -> str:
    result = shutil.which(name)
    if not result:
        raise RuntimeError(f"缺少 {name}。请安装 FFmpeg（含 ffprobe），重新打开 Agent 后运行 doctor。")
    return result


def duration(path: Path) -> float:
    result = subprocess.run(
        [executable('ffprobe'), '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'json', str(path)], check=True, capture_output=True, text=True,
    )
    value = float(json.loads(result.stdout)['format']['duration'])
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"媒体时长无效：{path}")
    return value


def audio_clip(source: Path, output: Path, start: float = 0,
               seconds: float | None = None, *, pcm: bool = False) -> None:
    if not math.isfinite(start) or start < 0 or (seconds is not None and (not math.isfinite(seconds) or seconds <= 0)):
        raise ValueError('音频片段起止时间无效')
    if output.exists():
        raise FileExistsError(f'拒绝覆盖音频：{output}')
    command = [executable('ffmpeg'), '-nostdin', '-hide_banner', '-loglevel', 'error',
               '-ss', str(start), '-i', str(source)]
    if seconds is not None:
        command += ['-t', str(seconds)]
    command += ['-map', '0:a:0', '-vn']
    command += ['-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le'] if pcm else ['-c:a', 'aac', '-b:a', '128k']
    command += ['-n', str(output)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        if not output.is_file() or not output.stat().st_size:
            raise RuntimeError('媒体工具没有生成有效音频')
    except Exception:
        output.unlink(missing_ok=True)
        raise
