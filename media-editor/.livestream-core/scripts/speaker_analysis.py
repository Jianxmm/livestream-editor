"""Evidence-bound analysis views; never rewrite source labels or transcript bytes."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from route_livestream import SPEAKER_HEADER_RE, file_sha256, find_replay, route_position_errors
from source_turns import blocks_from_text,contract_state

SOURCE = 'inputs/02_带时间码原稿.md'


def seconds(value: str) -> int:
    parts = [int(part) for part in value.split(':')]
    if len(parts) not in (2, 3) or parts[-1] >= 60 or (len(parts) == 3 and parts[-2] >= 60):
        raise ValueError('无效时间码：' + value)
    return sum(part * 60 ** index for index, part in enumerate(reversed(parts)))


def verified_solo_route(case: Path, route: object) -> bool:
    """Only bound acoustic evidence can override erroneous raw platform labels."""
    if not isinstance(route, dict) or route.get('status') != 'PASS' or route.get('mode') != 'solo' or str(route.get('acoustic_speaker_count')) != '1':
        return False
    try:
        if route_position_errors(case, route):
            return False
        replay = find_replay(case)
        for key, path in [('source', case/SOURCE), ('replay', replay)]:
            record = route.get(key, {})
            if record.get('path') != path.relative_to(case).as_posix() or record.get('sha256') != file_sha256(path):
                return False
        evidence = route.get('replay', {}).get('evidence', [])
        return isinstance(evidence, list) and len(evidence) >= 5
    except (OSError, ValueError, AttributeError, TypeError):
        return False


@dataclass(frozen=True)
class SpeakerAnalysis:
    source: str
    speaker_map: dict[str, str]
    raw_label_map: dict[str, str | None]
    block_identities: tuple[tuple[int, str], ...]
    raw_block_count: int

    @property
    def source_turn_count(self) -> int:
        return self.count_turns()

    def count_turns(self, end_seconds: int | None = None, source: str | None = None) -> int:
        text = self.source if source is None else source
        matches = list(SPEAKER_HEADER_RE.finditer(text))
        previous = None
        count = 0
        for index, match in enumerate(matches):
            start = seconds(match.group('time'))
            if end_seconds is not None and start >= end_seconds:
                continue
            end = matches[index+1].start() if index+1 < len(matches) else len(text)
            if not re.sub(r'<!--.*?-->', '', text[match.end():end], flags=re.S).strip():
                continue
            identity = match.group('label').replace(' ', '')
            if identity not in self.speaker_map:
                raise ValueError('发言轮次必须从已核身份的分析源计算')
            if identity != previous:
                count += 1
                previous = identity
        return count

    def with_ledger(self, text: str) -> str:
        """Return a transient ledger view for source matching, never a saved ledger."""
        ledger = json.loads(text)
        if not isinstance(ledger, dict):
            raise ValueError('编辑台账必须为对象')
        ledger = dict(ledger)
        ledger['speaker_map'] = dict(self.speaker_map)
        return json.dumps(ledger, ensure_ascii=False)

    def valid_ledger_map(self, mapping: object) -> bool:
        return isinstance(mapping, dict) and mapping == self.raw_label_map


def build_speaker_analysis(case: Path, source: str) -> SpeakerAnalysis | None:
    """Reconcile raw blocks only after complete current dual-speaker evidence.

    Legacy two-label cases without a route/provenance retain their old checks.
    Nonstandard labels and subtitle-derived cases cannot use this fallback.
    Pending/ambiguous blocks raise instead of pretending an identity is settled.
    """
    case = case.resolve()
    matches = list(SPEAKER_HEADER_RE.finditer(source))
    labels = {match.group('label').replace(' ', '') for match in matches}
    route_path = case/'_work/场次分流.json'
    ordered,contract_errors=contract_state(case)
    if contract_errors:raise ValueError('；'.join(contract_errors))
    required = ordered or len(labels) != 2 or '发言人未标注' in labels or (case/'_work/字幕导入记录.json').exists()
    if not route_path.exists() and not required:
        return None
    paths = {
        'calibration': case/'_work/说话人校准.json',
        'timeline': case/'_work/说话人时间轴.json',
        'conflicts': case/'_work/说话人冲突清单.md',
    }
    try:
        route = json.loads(route_path.read_text(encoding='utf-8-sig'))
        if route.get('status') != 'PASS' or route.get('mode') != 'dual' or str(route.get('acoustic_speaker_count')) != '2':
            raise ValueError('分析源需要已确认双人回放的本场分流')
        if route_position_errors(case, route):
            raise ValueError('五位置核验记录缺失、无效或已变化')
        replay = find_replay(case)
        source_path = case/SOURCE
        if source_path.read_text(encoding='utf-8-sig') != source:
            raise ValueError('分析源只能从当前原始转写创建')
        for key, path in [('source', source_path), ('replay', replay)]:
            record = route.get(key, {})
            if record.get('path') != path.relative_to(case).as_posix() or record.get('sha256') != file_sha256(path):
                raise ValueError('说话人分析的分流绑定已变化：' + key)
        calibration_text = paths['calibration'].read_text(encoding='utf-8-sig')
        timeline_text = paths['timeline'].read_text(encoding='utf-8-sig')
        conflicts_text = paths['conflicts'].read_text(encoding='utf-8-sig')
        calibration, timeline = json.loads(calibration_text), json.loads(timeline_text)
        if calibration.get('source_sha256') != file_sha256(replay):
            raise ValueError('身份锚点未绑定当前回放')
        for key, path in [('replay_sha256', replay), ('transcript_sha256', source_path), ('calibration_sha256', paths['calibration'])]:
            if timeline.get('provenance', {}).get(key) != file_sha256(path):
                raise ValueError('独立时间轴绑定已变化：' + key)
        # Import at call time so the shared preflight remains the single rule source.
        from audit_draft import audit_speaker_preflight
        findings = audit_speaker_preflight(
            source, (case/'outputs/02_待核问题清单.md').read_text(encoding='utf-8-sig'),
            calibration_text, timeline_text, conflicts_text,
            paths['calibration'], paths['timeline'], paths['conflicts'])
        errors = [finding.message for finding in findings if finding.level == 'ERROR' or finding.code == 'SPEAKER_CONFLICT_WEAK_CLOSURE']
        if errors:
            raise ValueError('说话人分析预检未完成：' + '；'.join(errors[:3]))
        identities = {str(row['speaker_id']): str(row['name']) for row in calibration['speakers']}
        if len(identities) != 2 or len(set(identities.values())) != 2:
            raise ValueError('需要两位不同的已核身份，不能用未知标签补造姓名')
        canonical = {identity: f'发言人{index}' for index, identity in enumerate(identities, 1)}
        identities_by_block=blocks_from_text(source) if ordered else None
        rows = ({row['source_block_id']:row for row in timeline['source_blocks']} if ordered else
                {(seconds(row['source_start']), row['source_label']):row for row in timeline['source_blocks']})
        segments = {row['id']: row for row in timeline['segments']}
        output, cursor, block_identities = [], 0, []
        for ordinal,match in enumerate(matches):
            start, label = seconds(match.group('time')), match.group('label').replace(' ', '')
            row = rows[identities_by_block[ordinal]['id']] if ordered else rows[(start, label)]
            identity = row.get('resolved_speaker_id')
            if row.get('status') not in {'一致', '声学归属', '真人已确认'} or identity not in identities:
                raise ValueError(f"{match.group('time')} 归属仍待核，不能按标签猜测身份")
            linked = [segments[segment_id] for segment_id in row['timeline_segment_ids']]
            if {segment.get('speaker_id') for segment in linked} != {identity}:
                raise ValueError(f"{match.group('time')} 含混合或不一致声音，须先逐块核实")
            if row['status'] in {'一致','声学归属'} and any(segment.get('uncertain') or segment.get('overlap') for segment in linked):
                raise ValueError(f"{match.group('time')} 含待核声音，不能标作一致")
            output.extend([source[cursor:match.start('label')], canonical[identity]])
            cursor = match.end('label')
            block_identities.append((start, identity))
        output.append(source[cursor:])
        if {identity for _, identity in block_identities} != set(identities):
            raise ValueError('原稿没有可核验的双人内容覆盖，不能仅靠分流补出另一人')
        default_map = calibration['source_label_default_map']
        raw_map = {label: identities[default_map[label]] if default_map[label] is not None else None for label in labels}
        return SpeakerAnalysis(''.join(output), {canonical[key]: name for key, name in identities.items()}, raw_map,
                               tuple(block_identities), len(matches))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError('说话人分析尚未完成：' + str(exc)) from exc
