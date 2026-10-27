#!/usr/bin/env python3
"""Install locally; explicit upgrades preserve the entire previous installation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
from build_release import release_files


def install(target: Path, *, upgrade: bool = False,
            backup_dir: Path | None = None, source: Path = ROOT) -> dict:
    raw_target = target.expanduser().absolute()
    if raw_target.is_symlink():
        raise ValueError('安装目标是符号链接；请明确实际目录后再安装，不自动替换链接。')
    target, source = raw_target.resolve(), source.resolve()
    files = release_files(source)
    if target == source or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('安装目标不能与下载目录互相包含')
    expected_hashes = {f.relative_to(source).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
                       for f in files}
    manifest = source / 'PACKAGE-MANIFEST.json'
    manifest_bytes = None
    if manifest.is_symlink() or (manifest.exists() and not manifest.is_file()):
        raise ValueError('下载包清单必须是普通文件，不能是目录或符号链接。')
    if manifest.is_file():
        manifest_bytes = manifest.read_bytes()
        meta = json.loads(manifest_bytes.decode('utf-8'))
        expected = {f.relative_to(source).as_posix() for f in files}
        if set(meta.get('files', {})) != expected:
            raise ValueError('下载包清单与白名单不一致，拒绝安装')
        for f in files:
            name = f.relative_to(source).as_posix()
            if meta['files'][name] != expected_hashes[name]:
                raise ValueError('下载包校验失败：' + name)
    backup = None
    if target.exists():
        if not target.is_dir():
            raise FileExistsError('安装目标已存在且不是目录')
        def unchanged(f: Path) -> bool:
            relative = f.relative_to(source)
            path = target / relative
            if any((target / Path(*relative.parts[:i])).is_symlink()
                   for i in range(1, len(relative.parts) + 1)):
                return False
            return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected_hashes[relative.as_posix()]
        installed_manifest = target / manifest.name
        manifest_unchanged = (manifest_bytes is None or
                              (not installed_manifest.is_symlink() and installed_manifest.is_file()
                               and installed_manifest.read_bytes() == manifest_bytes))
        if all(unchanged(f) for f in files) and manifest_unchanged:
            return {'target': str(target), 'changed': False, 'backup': None}
        if not upgrade:
            raise FileExistsError('已有不同版本或人工修改。升级请加 --upgrade，旧版会完整备份；素材始终放在独立工作目录。')
        backups = (backup_dir or Path.home() / '.agents/skill-backups').expanduser().resolve()
        if (backups.is_relative_to(target.parent) or target.is_relative_to(backups)
                or backups.is_relative_to(source) or source.is_relative_to(backups)):
            raise ValueError('备份目录必须放在技能扫描目录和下载目录之外')
        backups.mkdir(parents=True, exist_ok=True)
        version_file = target / 'VERSION'
        version = version_file.read_text(encoding='utf-8').strip() if version_file.is_file() else 'unknown'
        version = re.sub(r'[^A-Za-z0-9._-]', '_', version)[:40]
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = backups / (target.name + '-' + version + '-' + stamp)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.install-', dir=target.parent) as temp:
        stage = Path(temp) / 'livestream-editor'
        stage.mkdir()
        for f in files:
            dest = stage / f.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest)
            if hashlib.sha256(dest.read_bytes()).hexdigest() != expected_hashes[f.relative_to(source).as_posix()]:
                raise ValueError('安装文件复制校验失败，保留原安装：' + f.relative_to(source).as_posix())
        if manifest_bytes is not None:
            (stage / manifest.name).write_bytes(manifest_bytes)
        if backup:
            # shutil.move also supports a backup directory on another volume.
            shutil.move(str(target), str(backup))
        try:
            stage.rename(target)
        except Exception:
            if backup and not target.exists():
                shutil.move(str(backup), str(target))
            raise
    return {'target': str(target), 'changed': True, 'backup': str(backup) if backup else None}


def main() -> int:
    p = argparse.ArgumentParser(description='安装直播整理 Skill；显式升级时完整备份旧版')
    p.add_argument('--target', type=Path, default=Path.home() / '.agents/skills/livestream-editor')
    p.add_argument('--upgrade', action='store_true', help='备份已安装的不同版本，再安装新版')
    p.add_argument('--backup-dir', type=Path, help='旧版备份目录；默认 ~/.agents/skill-backups')
    a = p.parse_args()
    try:
        result = install(a.target, upgrade=a.upgrade, backup_dir=a.backup_dir)
        print('INSTALL=PASS\n' + json.dumps(result, ensure_ascii=False, indent=2))
        print('新建对话并使用 $livestream-editor；未显示时重启客户端。')
        return 0
    except Exception as exc:
        print('INSTALL=BLOCKED\n' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
