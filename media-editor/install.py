#!/usr/bin/env python3
"""Install an extracted, integrity-checked release without replacing an existing skill."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent


def install(source: Path, target: Path) -> dict:
    source = source.resolve()
    raw_target = target.expanduser().absolute()
    if raw_target.is_symlink():
        raise ValueError('安装位置是符号链接，不自动替换')
    target = raw_target.resolve()
    if target.exists():
        raise FileExistsError('安装位置已存在；保留原文件，请选择其他目录或先人工备份')
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('安装目录不能与解压目录互相包含')
    manifest = source / 'PACKAGE-MANIFEST.json'
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError('缺少下载包完整性清单；请从正式 ZIP 解压后安装')
    meta = json.loads(manifest.read_text(encoding='utf-8'))
    files = meta.get('files')
    if meta.get('name') != 'media-editor' or not isinstance(files, dict) or not files:
        raise ValueError('安装包清单无效')
    for name, digest in files.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ValueError('安装包路径或指纹无效')
        rel = PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts or '\\' in name or rel.as_posix() != name:
            raise ValueError('安装包路径无效')
        path = source / rel
        if any((source / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)):
            raise ValueError('安装包包含符号链接')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('安装包文件校验失败：' + name)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.media-editor-install-', dir=target.parent) as temp:
        stage = Path(temp) / 'media-editor'; stage.mkdir()
        for name, digest in files.items():
            dest = stage / PurePosixPath(name); dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / PurePosixPath(name), dest)
            if hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
                raise ValueError('复制后校验失败：' + name)
        shutil.copyfile(manifest, stage / manifest.name)
        if target.exists():
            raise FileExistsError('安装位置在准备期间已出现；原文件未覆盖')
        stage.rename(target)
    return {'target': str(target), 'files': len(files), 'status': 'installed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='安装完整性校验过的文稿整理 Skill，不覆盖已有版本')
    parser.add_argument('--target', type=Path, default=Path.home() / '.agents/skills/media-editor')
    args = parser.parse_args()
    try:
        print(json.dumps(install(ROOT, args.target), ensure_ascii=False, indent=2))
        print('新建对话使用 $media-editor；未显示时重启客户端。')
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print('INSTALL=BLOCKED: ' + str(exc)); raise SystemExit(1)
