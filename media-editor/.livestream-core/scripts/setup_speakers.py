"""Optional isolated speaker runtime. Downloads only on explicit invocation."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

from route_livestream import file_sha256
from workspace import outside_install, write_json, ROOT

PACKAGES = ['numpy==2.2.6', 'sherpa-onnx==1.13.8']

def default_runtime() -> Path:
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local'))) / 'livestream-editor'
    return Path.home()/'.cache/livestream-editor'

def runtime_python(runtime: Path) -> Path:
    return runtime/'venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')

def setup(runtime: Path, python: str | None = None) -> dict:
    runtime=runtime.expanduser().resolve();outside_install(runtime)
    runtime.mkdir(parents=True,exist_ok=True)
    interpreter=runtime_python(runtime)
    if not interpreter.exists():
        selected=python or shutil.which('python3.12') or shutil.which('python3.11') or sys.executable
        version=json.loads(subprocess.check_output([selected,'-c','import sys,json;print(json.dumps(list(sys.version_info[:2])))'],text=True))
        if not (3,10) <= tuple(version) < (3,14):
            raise RuntimeError('双人模型运行时需要 Python 3.10–3.13。安装 Python 3.12 后重试，或用 --python 指定解释器。基础整理工具仍可用当前 Python。')
        subprocess.run([selected,'-m','venv',str(runtime/'venv')],check=True)
    subprocess.run([str(interpreter),'-m','pip','install','--disable-pip-version-check','--only-binary=:all:',*PACKAGES],check=True)
    config=json.loads((ROOT/'assets/models.json').read_text(encoding='utf-8'))
    model_dir=runtime/'models';model_dir.mkdir(exist_ok=True)
    for item in config['models']:
        output=model_dir/item['output']
        if output.exists():
            if file_sha256(output)!=item['model_sha256']:
                raise ValueError(f'已有模型校验失败，保留现场：{output}')
            continue
        print('下载公开模型：'+item['kind'],flush=True)
        with tempfile.TemporaryDirectory(dir=model_dir) as temp:
            archive=Path(temp)/'download'
            request=urllib.request.Request(item['url'],headers={'User-Agent':'livestream-editor/0.1'})
            with urllib.request.urlopen(request,timeout=60) as response, archive.open('wb') as f:
                shutil.copyfileobj(response,f)
            if file_sha256(archive)!=item['download_sha256']:
                raise ValueError('模型下载 SHA-256 不匹配；拒绝使用，未替换已有模型。')
            staged=Path(temp)/'model.onnx'
            if 'member' in item:
                with tarfile.open(archive,'r:bz2') as tar:
                    member=tar.getmember(item['member'])
                    if not member.isfile():raise ValueError('模型归档成员不是普通文件')
                    with tar.extractfile(member) as src,staged.open('wb') as dst:shutil.copyfileobj(src,dst)
            else:
                archive.rename(staged)
            if file_sha256(staged)!=item['model_sha256']:raise ValueError('模型文件校验失败')
            staged.rename(output)
    info={'schema_version':1,'packages':PACKAGES,'models':config['models']}
    write_json(runtime/'runtime.json',info)
    return info

def main() -> int:
    p=argparse.ArgumentParser(description='安装隔离的双人声学运行时及校验固定公开模型；约百 MB，需联网')
    p.add_argument('--runtime',type=Path,default=default_runtime());p.add_argument('--python')
    a=p.parse_args()
    try:
        setup(a.runtime,a.python);print('SPEAKER_SETUP=PASS\nruntime='+str(a.runtime));return 0
    except Exception as exc:
        print(f'SPEAKER_SETUP=BLOCKED\n{exc}',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
