#!/usr/bin/env python3
"""Build an isolated combined skill from explicit files and the live core allowlist."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parent
WRAPPER=('SKILL.md','README.md','VALIDATION.md','THIRD_PARTY.md','LICENSE','VERSION','agents/openai.yaml','references/general.md','references/acquisition.md',
         'scripts/media_intake.py','build_package.py','install.py','tests/test_install_package.py','tests/test_media_intake.py')

def build(output: Path) -> dict:
    output=output.expanduser().resolve()
    if output.exists(): raise FileExistsError('目标已存在，拒绝覆盖')
    core=(ROOT/'.livestream-core').resolve()
    spec=importlib.util.spec_from_file_location('core_release',core/'scripts/build_release.py')
    if spec is None or spec.loader is None: raise ValueError('无法读取直播核心打包器')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    files={}
    for name in WRAPPER:
        path=ROOT/name
        if not path.is_file() or path.is_symlink(): raise ValueError('入口文件无效: '+name)
        files[name]=path.read_bytes()
    for path in module.release_files(core):
        files['.livestream-core/'+path.relative_to(core).as_posix()]=path.read_bytes()
    hashes={name:hashlib.sha256(content).hexdigest() for name,content in sorted(files.items())}
    version=files['VERSION'].decode('utf-8').strip()
    manifest=json.dumps({'name':'media-editor','version':version,'files':hashes},ensure_ascii=False,indent=2).encode()+b'\n'
    output.parent.mkdir(parents=True,exist_ok=True)
    try:
        with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as z:
            for name,body in [*sorted(files.items()),('PACKAGE-MANIFEST.json',manifest)]:
                info=zipfile.ZipInfo('media-editor/'+name,(2026,1,1,0,0,0))
                info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
                z.writestr(info,body)
    except Exception:
        output.unlink(missing_ok=True);raise
    return {'path':str(output),'files':len(files)+1,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try: print(json.dumps(build(args.output),ensure_ascii=False,indent=2))
    except (OSError,ValueError) as exc: print('PACKAGE=BLOCKED: '+str(exc));raise SystemExit(1)
