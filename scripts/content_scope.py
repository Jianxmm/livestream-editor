"""Traceable optional C-content exclusions stored in the existing edit ledger."""
from dataclasses import dataclass, field
import json
from pathlib import Path
import re

from route_livestream import file_sha256, SPEAKER_HEADER_RE
from review_validation import valid_review_date

CATEGORIES = {'设备调试', '无信息互动', '重复促销', '纯重复', '无法恢复ASR', '关播尾声'}


def comparable_excerpt(text: str) -> str:
    """Allow punctuation/spacing cleanup, without admitting unrelated prose."""
    return ''.join(character for character in text if character.isalnum())


@dataclass
class ContentScope:
    source: str
    full_draft: str
    exclusions: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def apply_to_analysis(self, text: str) -> str:
        if self.errors:
            return text
        for row in self.exclusions:
            text = text.replace(row['source_excerpt'], '', 1)
        return text


def seconds(value: str) -> int:
    if not re.fullmatch(r'(?:\d{1,2}:)?\d{2}:\d{2}', value):
        raise ValueError('无效时间码：' + value)
    parts = [int(x) for x in value.split(':')]
    if parts[-1] >= 60 or (len(parts) == 3 and parts[-2] >= 60):
        raise ValueError('无效时间码：' + value)
    return sum(n * 60 ** i for i, n in enumerate(reversed(parts)))


def coverage_rows(text: str) -> list[tuple[int, int, str]]:
    rows = []
    for line in text.splitlines():
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        match = re.fullmatch(r'((?:\d{1,2}:)?\d{2}:\d{2})\s*[–—-]\s*((?:\d{1,2}:)?\d{2}:\d{2})', cells[0])
        grades = [cell for cell in cells[1:] if cell in {'A', 'B', 'C'}]
        if match and len(grades) == 1:
            rows.append((seconds(match[1]), seconds(match[2]), grades[0]))
    return rows


def read_content_scope(case: Path, source: str, full_draft: str, *, handoff: bool = False) -> ContentScope:
    result = ContentScope(source, full_draft)
    ledger_path = case/'_work/编辑决策台账.json'
    if not ledger_path.is_file():
        return result
    try:
        ledger = json.loads(ledger_path.read_text(encoding='utf-8-sig'))
        scope = ledger.get('content_scope')
        if scope is None:
            return result  # Original ratios remain unchanged when no exclusion is declared.
        if not isinstance(scope, dict) or scope.get('version') != 1:
            raise ValueError('content_scope 需要 version=1 对象')
        entries = scope.get('exclusions')
        if not isinstance(entries, list):
            raise ValueError('content_scope.exclusions 必须为数组')
        if not entries:
            return result
        for key, path in [('source_sha256', case/'inputs/02_带时间码原稿.md'), ('full_draft_sha256', case/'_work/完整编辑底稿.md')]:
            if not path.is_file() or scope.get(key) != file_sha256(path):
                raise ValueError(f'有效内容范围未绑定当前文件：{path.name}')
        coverage = coverage_rows((case/'_work/内容覆盖表.md').read_text(encoding='utf-8-sig'))
        matches = list(SPEAKER_HEADER_RE.finditer(source))
        blocks = [(seconds(m.group('time')), source[m.end():matches[i+1].start() if i+1<len(matches) else len(source)]) for i,m in enumerate(matches)]
        source_times = {start for start, _ in blocks}
        used_ids, removed_source, removed_full = set(), [], []
        for index, row in enumerate(entries, 1):
            if not isinstance(row, dict):
                raise ValueError(f'第{index}项不是对象')
            key = row.get('id')
            if not isinstance(key, str) or not key.strip() or key in used_ids:
                raise ValueError('排除项需要唯一且非空的id')
            used_ids.add(key)
            if row.get('category') not in CATEGORIES:
                raise ValueError(f'{key} 不是允许的C级类别')
            start, end = seconds(str(row.get('start', ''))), seconds(str(row.get('end', '')))
            if start not in source_times or end not in source_times or start > end:
                raise ValueError(f'{key} 必须定位到实际原稿块且时间不倒序')
            if not any(a <= start and end <= b and grade == 'C' for a,b,grade in coverage):
                raise ValueError(f'{key} 在覆盖表中没有对应的C级区间')
            if any(a <= end and start <= b and grade in {'A','B'} for a,b,grade in coverage):
                raise ValueError(f'{key} 与覆盖表A/B级有效内容区间重叠')
            excerpt = row.get('source_excerpt')
            if not isinstance(excerpt, str) or not excerpt.strip() or SPEAKER_HEADER_RE.search(excerpt):
                raise ValueError(f'{key} 缺少连续原文片段，或片段包含时间标题')
            selected = '\n\n'.join(text for second,text in blocks if start <= second <= end)
            if excerpt not in selected or source.count(excerpt) != 1:
                raise ValueError(f'{key} 原文片段不能在声明区间中唯一定位')
            full_excerpt = row.get('full_draft_excerpt', '')
            if not isinstance(full_excerpt, str) or (full_excerpt and full_draft.count(full_excerpt) != 1):
                raise ValueError(f'{key} 完整底稿片段不存在或不能唯一定位')
            if full_excerpt and (not comparable_excerpt(full_excerpt) or comparable_excerpt(full_excerpt) not in comparable_excerpt(excerpt)):
                raise ValueError(f'{key} 底稿排除片段必须对应同一C级原文，不能连带扣除其他内容')
            if not full_excerpt and excerpt in full_draft:
                raise ValueError(f'{key} 底稿仍有该内容，请明确底稿片段；不能只调整原稿分母')
            for prior in removed_source:
                if excerpt in prior or prior in excerpt:
                    raise ValueError(f'{key} 排除原文与其他项重复或嵌套')
            for prior in removed_full:
                if full_excerpt and (full_excerpt in prior or prior in full_excerpt):
                    raise ValueError(f'{key} 排除底稿与其他项重复或嵌套')
            basis = row.get('basis', '')
            if not isinstance(basis, str) or len(basis.strip()) < 4 or re.search(r'待填|待确认|填写依据', basis):
                raise ValueError(f'{key} 缺少实际删除依据')
            status = row.get('status')
            confirmed = status in {'已回听','已当事人确认'}
            if confirmed:
                reviewer = row.get('reviewer', '')
                if not isinstance(reviewer, str) or not reviewer.strip() or re.search(r'待填|待定|未知', reviewer) or not valid_review_date(row.get('review_date')):
                    raise ValueError(f'{key} 确认记录缺少真实人员或有效日期')
            elif status != '待确认':
                raise ValueError(f'{key} 状态必须明确为待确认、已回听或已当事人确认')
            elif handoff:
                raise ValueError(f'{key} 的C级排除尚未真人确认，不可发送或发布')
            else:
                result.warnings.append(f'{key} 的C级排除待真人确认；当前仅按暂定有效内容预检')
            removed_source.append(excerpt)
            if full_excerpt:
                removed_full.append(full_excerpt)
            result.exclusions.append(row)
        for excerpt in removed_source:
            result.source = result.source.replace(excerpt, '', 1)
        for excerpt in removed_full:
            result.full_draft = result.full_draft.replace(excerpt, '', 1)
        bodies = list(SPEAKER_HEADER_RE.finditer(result.source))
        spoken = ''.join(result.source[m.end():bodies[i+1].start() if i+1<len(bodies) else len(result.source)] for i,m in enumerate(bodies))
        if not spoken.strip():
            raise ValueError('排除后没有有效原文，不能把整场内容排空取得通过')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result.errors.append(f'有效内容范围无效：{exc}')
        result.source, result.full_draft, result.exclusions = source, full_draft, []
    return result
