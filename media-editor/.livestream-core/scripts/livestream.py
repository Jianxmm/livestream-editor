#!/usr/bin/env python3
"""One CLI for a standalone Skill; works from any current directory."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys

COMMANDS = {
    'route': 'route_livestream.py', 'full-audio': 'extract_full_audio.py',
    'review-clips': 'extract_review_clips.py', 'source-index': 'build_source_review_index.py',
    'review-index': 'build_review_index.py', 'review': 'track_human_review.py',
    'source-preflight': 'source_preflight.py',
    'reading-index': 'build_reading_index.py',
    'profile': 'personal_profile.py',
    'audit': 'audit_package.py', 'speakers': 'speaker_pipeline.py',
    'setup-speakers': 'setup_speakers.py', 'package': 'build_release.py',
    'self-test': 'self_test.py',
}

def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print('用法：python scripts/livestream.py <命令> [参数]\n命令：doctor init templates probe export ' + ' '.join(COMMANDS))
        return 0
    command, args = sys.argv[1], sys.argv[2:]
    if command in ('doctor', 'init', 'templates', 'probe', 'export'):
        script = 'workspace.py'; args = [command, *args]
    elif command in COMMANDS:
        script = COMMANDS[command]
    else:
        print('未知命令，请运行 --help', file=sys.stderr); return 2
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable, str(Path(__file__).parent / script), *args], env=env).returncode

if __name__ == '__main__':
    raise SystemExit(main())
