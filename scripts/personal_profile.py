"""Optional local vocabulary and reference materials, frozen per case."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROFILE_FILES = ('README.md', '01_专用词库.md', '02_参考案例与偏好.md')
RECORD = '_work/个人资料使用记录.json'
SNAPSHOT = '_work/个人资料'
EMPTY = {'', '—', '-', '待填写', '待补充', '暂无', '无'}
UNVERIFIED_EVIDENCE = {'待确认', '未确认', '待核实', '未核实', '尚未核实', '待回听',
                       '待提供', '未知', '不详', '无依据', '没有依据', 'AI判断', 'AI推断',
                       'AI猜测', 'AI生成', '模型判断', '模型推断', '机器转写', '机器识别',
                       '上下文推断', '上下文推测', '根据上下文推断', '根据上下文推测'}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def external(path: Path) -> Path:
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise ValueError('个人资料目录不能是符号链接，请指定实际目录。')
    resolved = path.resolve()
    if resolved.is_relative_to(ROOT) or ROOT.is_relative_to(resolved):
        raise ValueError('个人资料必须保存在 Skill 安装目录之外，且不能包含安装目录。')
    return resolved


def init_profile(directory: Path) -> dict:
    directory = external(directory)
    if directory.exists() and not directory.is_dir():
        raise FileExistsError('个人资料路径已存在且不是文件夹。')
    # Validate all destinations before filling missing files; existing edits always win.
    for name in PROFILE_FILES:
        target = directory / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError('资料文件类型异常：' + name)
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    for name in PROFILE_FILES:
        target = directory / name
        try:
            with target.open('xb') as stream:
                stream.write((ROOT / 'assets/profile' / name).read_bytes())
            created.append(name)
        except FileExistsError:
            pass
    return {'directory': str(directory), 'created': created}


def confirmed_rows(text: str) -> list[str]:
    rows, aliases = [], {}
    fence = None
    for line in text.splitlines():
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line)
        if marker:
            token, suffix = marker.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not suffix.strip():
                fence = None
            continue
        if fence is not None:
            continue  # Format examples are not verified vocabulary entries.
        if not line.lstrip().startswith('|'):
            continue
        cells = [x.strip().strip('`') for x in line.strip().strip('|').split('|')]
        if not cells or cells[0] in EMPTY or cells[0] == '规范写法' or set(cells[0]) <= {'-', ':'}:
            continue
        if len(cells) != 6 or cells[4] not in {'已确认', '待确认'}:
            raise ValueError('专用词库需六列，状态使用“已确认”或“待确认”：' + cells[0])
        if cells[4] != '已确认':
            continue
        evidence = re.sub(r'\s+', '', cells[5]).strip('。.!！')
        if cells[5] in EMPTY or evidence in UNVERIFIED_EVIDENCE:
            raise ValueError('已确认专名缺少依据：' + cells[0])
        for alias in [cells[0], *re.split(r'[、,，/]+', cells[2])]:
            alias = alias.strip()
            if alias in EMPTY:
                continue
            if alias in aliases and aliases[alias] != cells[0]:
                raise ValueError('专名写法冲突，请先核实：' + alias)
            aliases[alias] = cells[0]
        rows.append('| ' + ' | '.join(cells) + ' |')
    return rows


def inspect_profile(directory: Path) -> dict:
    directory = external(directory)
    for name in PROFILE_FILES:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('个人资料缺失或为链接：' + name)
    rows = confirmed_rows((directory / '01_专用词库.md').read_text(encoding='utf-8-sig'))
    return {'directory': str(directory), 'confirmed_terms': len(rows),
            'note': '只检查结构和确认依据是否填写，不证明内容真实。'}


def snapshot_profile(case: Path, directory: Path) -> dict:
    directory = external(directory)
    inspect_profile(directory)
    case = case.resolve()
    destination, record = case / SNAPSHOT, case / RECORD
    data = {'version': 1, 'source_directory': str(directory),
            'files': {name: digest(directory / name) for name in PROFILE_FILES}}
    if destination.exists() or record.exists() or destination.is_symlink() or record.is_symlink():
        if validate_snapshot(case):
            raise ValueError('已有个人资料快照损坏，保留现场，不能自动替换。')
        if record.is_file() and json.loads(record.read_text(encoding='utf-8')) == data:
            return data
        raise FileExistsError('本场已绑定另一版个人资料，禁止用更新后的资料覆盖历史快照。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        stage = Path(temp) / '个人资料'
        stage.mkdir()
        for name in PROFILE_FILES:
            shutil.copyfile(directory / name, stage / name)
            if digest(stage / name) != data['files'][name]:
                raise RuntimeError('复制过程中个人资料发生变化：' + name)
        stage.rename(destination)
    try:
        with record.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    except Exception:
        shutil.rmtree(destination)
        raise
    return data


def validate_snapshot(case: Path) -> list[str]:
    folder, record = case / SNAPSHOT, case / RECORD
    if not folder.exists() and not record.exists() and not folder.is_symlink() and not record.is_symlink():
        return []  # Personalization is optional, including for historical cases.
    try:
        if folder.is_symlink() or record.is_symlink():
            raise ValueError('快照与记录不能是符号链接')
        data = json.loads(record.read_text(encoding='utf-8-sig'))
        if data.get('version') != 1 or set(data.get('files', {})) != set(PROFILE_FILES):
            raise ValueError('资料清单不完整或版本无效')
        for name, expected in data['files'].items():
            path = folder / name
            if path.is_symlink() or not path.is_file() or digest(path) != expected:
                raise ValueError('本场个人资料快照缺失或已变化：' + name)
        confirmed_rows((folder / '01_专用词库.md').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return ['个人资料记录无效：' + str(exc)]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description='建立和检查本地词库、参考案例与整理偏好')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'check'):
        sub.add_parser(name).add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        result = init_profile(args.directory) if args.command == 'init' else inspect_profile(args.directory)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print('PROFILE=BLOCKED\n' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
