"""Create a plain-language entry page without rewriting any editorial content."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from route_livestream import find_replay, file_sha256

NAME = '00_从这里开始.md'
MAIN = '01_公众号可发布稿.md'
ISSUES = '02_待核问题清单.md'
REPORT = '03_编辑质检报告.md'


def build_reading_index(case: Path) -> Path:
    case = case.expanduser().resolve()
    route = json.loads((case/'_work/场次分流.json').read_text(encoding='utf-8-sig'))
    mode = route.get('mode')
    if route.get('status') != 'PASS' or mode not in {'solo', 'dual'}:
        raise ValueError('还没有确认这场直播的说话情况，请先核对回放。')
    replay = find_replay(case)
    for key, path in [('replay', replay), ('source', case/'inputs/02_带时间码原稿.md')]:
        item = route.get(key, {})
        if item.get('path') != path.relative_to(case).as_posix() or item.get('sha256') != file_sha256(path):
            raise ValueError('素材与之前核对的版本不一致，请先确认使用哪一份。')
    output = case/'outputs'
    required = [MAIN, ISSUES, REPORT]
    if mode == 'solo':
        required.append('04_整场音频.m4a')
    missing = [name for name in required if not (output/name).is_file()]
    if missing:
        raise FileNotFoundError('还有文件没有整理好：' + '、'.join(missing))
    main_label = '单人分享整理稿' if mode == 'solo' else '双人访谈整理稿'
    lines = [
        '# 这场直播的整理结果', '',
        f'先打开[整理好的正文]({MAIN})，再查看有没有需要你确认的地方。', '',
        '| 你想看什么 | 打开这里 |',
        '| --- | --- |',
        f'| 阅读、修改整理好的文字 | [{main_label}]({MAIN}) |',
        f'| 核对听不清或拿不准的内容 | [需要你确认的地方]({ISSUES}) |',
        f'| 了解做过哪些调整和检查，选读 | [整理说明]({REPORT}) |',
    ]
    if mode == 'solo':
        lines.append('| 对照声音阅读 | [整场音频](04_整场音频.m4a) |')
    lines += ['', '## 接下来怎么做', '',
              '先读正文，再按问题清单中的位置核对原话。确认相关问题已处理、正文没有误解原意后，再决定是否发送或发布。', '',
              '如果你已经修改过正文，后续继续整理时请明确使用哪一份，不要用旧稿覆盖。', '',
              '这页只是阅读入口，不代表正文已经由真人核对。', '']
    content = '\n'.join(lines)
    target = output/NAME
    if target.is_symlink():
        raise FileExistsError('阅读入口是一个链接，请先核对它实际指向哪里。')
    if target.exists():
        if target.read_text(encoding='utf-8-sig') == content:
            return target
        raise FileExistsError('阅读入口已有不同内容，可能经过人工修改；请先保留并比较，不自动覆盖。')
    with target.open('x', encoding='utf-8', newline='\n') as f:
        f.write(content)
    return target


def main() -> int:
    p = argparse.ArgumentParser(description='为整理结果生成一个容易阅读的入口页，不改正文')
    p.add_argument('case', type=Path)
    args = p.parse_args()
    try:
        print('READING_INDEX=PASS\n' + str(build_reading_index(args.case)))
        return 0
    except Exception as exc:
        print('READING_INDEX=BLOCKED\n' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
