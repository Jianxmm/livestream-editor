"""Allowlisted, reproducible distribution; never traverses a case directory."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def release_files(root: Path = ROOT) -> list[Path]:
    names=json.loads((root/'release-files.json').read_text(encoding='utf-8'))
    if not isinstance(names,list) or not all(isinstance(n,str) and n.strip() for n in names) or len(names)!=len(set(names)):
        raise ValueError('分发白名单重复或无效')
    files=[]
    for name in names:
        relative=PurePosixPath(name)
        if relative.as_posix()=='PACKAGE-MANIFEST.json':
            raise ValueError('PACKAGE-MANIFEST.json 由打包器自动生成，不能加入源文件白名单')
        if relative.name.startswith('.env') or relative.name in {'id_rsa','id_ed25519','credentials.json','hosts.yml'} or relative.suffix.lower() in {'.pem','.key','.p12','.pfx'}:
            raise ValueError('账号或密钥文件不能分发：'+name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or any(p in {'.git','cases','validation_campaigns','__pycache__','.venv','我的文稿资料','.local'} for p in relative.parts):
            raise ValueError('不允许的分发路径：'+name)
        path=root/relative
        if not path.is_file() or path.is_symlink() or any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1,len(relative.parts))):
            raise ValueError('白名单文件不存在或经过符号链接：'+name)
        if not path.resolve().is_relative_to(root.resolve()):raise ValueError('资源越界')
        if path.suffix in {'.md','.py','.json','.yaml','.yml','.txt'}:
            text=path.read_text(encoding='utf-8')
            forbidden=['.codex'+'/skills/edit-']
            if (any(s in text for s in forbidden) or re.search(r'/Users/[^/\s]+/',text)) and path.name!='build_release.py':
                raise ValueError('发现本机或私有项目依赖：'+name)
        files.append(path)
    if sum(p.name=='SKILL.md' for p in files)!=1:raise ValueError('分发包必须恰好有一个 SKILL.md')
    return files

def build(output: Path, root: Path = ROOT) -> dict:
    output=output.resolve()
    if output.exists():raise FileExistsError('发布包已存在，拒绝覆盖：'+str(output))
    files=release_files(root)
    # Hash and write the same bytes; a concurrent source edit must not yield a
    # ZIP whose own integrity manifest is already invalid.
    payloads={p.relative_to(root).as_posix():p.read_bytes() for p in files}
    version_bytes=payloads['VERSION'] if 'VERSION' in payloads else (root/'VERSION').read_bytes()
    metadata={'name':'livestream-editor','version':version_bytes.decode('utf-8-sig').strip(),
              'files':{name:hashlib.sha256(data).hexdigest() for name,data in payloads.items()}}
    output.parent.mkdir(parents=True,exist_ok=True)
    try:
        with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,data in payloads.items():
                info=zipfile.ZipInfo('livestream-editor/'+name,date_time=(2026,1,1,0,0,0))
                info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
                archive.writestr(info,data)
            info=zipfile.ZipInfo('livestream-editor/PACKAGE-MANIFEST.json',date_time=(2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            archive.writestr(info,json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    except Exception:
        output.unlink(missing_ok=True);raise
    return {'path':str(output),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':len(files)+1}

def main() -> int:
    p=argparse.ArgumentParser(description='按分发白名单构建 ZIP，不包含个人案例或模型')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    try:print(json.dumps(build(a.output),ensure_ascii=False,indent=2));return 0
    except Exception as exc:print('PACKAGE=BLOCKED\n'+str(exc));return 1

if __name__=='__main__':raise SystemExit(main())
