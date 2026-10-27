"""Explicit ordered-turn contract. Source bytes and timestamps are never rewritten."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from route_livestream import SPEAKER_HEADER_RE,file_sha256

CONTRACT='ordered-turns-v1'
INDEX=Path('_work/原稿逐块索引.json')
SOURCE=Path('inputs/02_带时间码原稿.md')


def blocks_from_text(text):
    matches=list(SPEAKER_HEADER_RE.finditer(text));rows=[]
    for ordinal,match in enumerate(matches,1):
        parts=[int(x) for x in match.group('time').split(':')]
        if parts[-1]>=60 or (len(parts)==3 and parts[-2]>=60):raise ValueError('原稿时间码超出合法范围')
        second=sum(v*60**i for i,v in enumerate(reversed(parts)))
        stop=matches[ordinal].start() if ordinal<len(matches) else len(text)
        raw=text[match.start():stop]
        fingerprint=hashlib.sha256(raw.encode()).hexdigest()
        rows.append({'id':f'B{ordinal:05d}-{fingerprint[:12]}','ordinal':ordinal,
                     'label':match.group('label').replace(' ',''),'timecode':match.group('time'),
                     'start_seconds':second,'source_line':text.count('\n',0,match.start())+1,
                     'block_sha256':fingerprint})
    if any(b['start_seconds']<a['start_seconds'] for a,b in zip(rows,rows[1:])):
        raise ValueError('原稿时间码倒退，不能按逐段合同重排或改时间')
    counts=Counter(row['start_seconds'] for row in rows)
    for row in rows:row['same_second']=counts[row['start_seconds']]>1
    return rows


def index_for(source):
    return {'schema_version':1,'contract':CONTRACT,'source':SOURCE.as_posix(),
            'source_sha256':file_sha256(source),'blocks':blocks_from_text(source.read_text(encoding='utf-8-sig')),
            'note':'原时间和原顺序保留；同秒只是检索分组，不证明同时发声、声学边界或人物归属。'}


def contract_state(case):
    """Return enabled/errors. A missing or changed record never enables the contract."""
    manifest=case/'_work/输入来源.json';index=case/INDEX
    try:
        value=json.loads(manifest.read_text()) if manifest.is_file() else {}
        if not isinstance(value,dict):raise ValueError('输入来源记录不是对象')
        kind=value.get('transcript_contract','strict')
        if kind=='strict':
            return False,(['逐块索引缺少对应的输入合同'] if index.exists() else [])
        if kind!=CONTRACT:raise ValueError('未知原稿输入合同')
        expected=index_for(case/SOURCE)
        if json.loads(index.read_text())!=expected:raise ValueError('逐块索引与当前原稿不一致')
        if value.get('turn_index_sha256')!=file_sha256(index):raise ValueError('逐块索引输入指纹不一致')
        return True,[]
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return False,['逐段原稿合同无效：'+str(exc)]


def timeline_identity_errors(source,rows):
    """Tied blocks require independent IDs, exact order, and explicit uncertainty."""
    expected=blocks_from_text(source)
    if not any(row['same_second'] for row in expected):return []
    errors=[];tied_conflicts=[]
    if not isinstance(rows,list) or len(rows)!=len(expected):return ['同秒原稿时间轴没有完整覆盖独立块编号']
    for block,row in zip(expected,rows):
        if not isinstance(row,dict) or row.get('source_block_id')!=block['id']:
            errors.append('同秒原稿块编号缺失、重排或重复');continue
        if row.get('source_label')!=block['label'] or row.get('source_start')!=block['timecode']:
            errors.append('原稿块编号绑定的标签或时间变化')
        if block['same_second']:
            tied_conflicts.append(row.get('conflict_id'))
            if row.get('timing_ambiguous') is not True:errors.append('同秒块必须保留边界待核标记')
            if row.get('status') in {'一致','声学归属'}:errors.append('同秒块不能仅凭自动对齐确认人物归属')
    if any(not isinstance(c,str) or not c for c in tied_conflicts) or len({str(c) for c in tied_conflicts})!=len(tied_conflicts):errors.append('每个同秒块必须保留独立的归属待核编号')
    return list(dict.fromkeys(errors))
