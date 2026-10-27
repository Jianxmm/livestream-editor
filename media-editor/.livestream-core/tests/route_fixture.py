"""Five-position route metadata for disposable tests; bytes are not real audio."""
from __future__ import annotations

import json
from pathlib import Path

from route_livestream import POSITIONS, SPEAKER_HEADER_RE, file_sha256, find_replay


def observations_for(case: Path) -> list[dict]:
    source = (case/'inputs/02_带时间码原稿.md').read_text(encoding='utf-8-sig')
    times = []
    for match in SPEAKER_HEADER_RE.finditer(source):
        values = [int(part) for part in match.group('time').split(':')]
        times.append(sum(value * 60 ** index for index, value in enumerate(reversed(values))))
    length = float(max(180, max(times, default=0) + 60))
    folder = case/'_work/分流试听'
    folder.mkdir(parents=True, exist_ok=True)
    clips, observations = [], []
    for index, (position, fraction) in enumerate(zip(POSITIONS, [0, .25, .5, .75, 1]), 1):
        start = min(max(0, length-20), length*fraction)
        span = min(20, length-start)
        name = f'fixture-{index:02d}.m4a'
        path = folder/name
        path.write_bytes(f'Synthetic fixture only; not playable audio. {position} {start} {span}'.encode('utf-8'))
        clips.append({'position':position, 'file':name, 'start_seconds':start,
                      'duration_seconds':span, 'audio_sha256':file_sha256(path)})
        observations.append({'position':position, 'start_seconds':start, 'end_seconds':start+span,
                             'method':'真人回听（仅为合成夹具，非真实核验）',
                             'finding':f'{position}合成观察记录，不代表真实声音判断。'})
    manifest = {'source_sha256':file_sha256(find_replay(case)), 'source_duration_seconds':length,
                'unique_clip_count':5, 'clips':clips, 'fixture_note':'纯合成测试，未播放或核验真实回放。'}
    (folder/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return observations
