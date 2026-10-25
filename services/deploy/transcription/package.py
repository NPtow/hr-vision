#!/usr/bin/env python3
"""Deterministic source-only build context, with SHA256 manifest. No secrets."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent


def package(output):
    output=Path(output).resolve()
    if output.is_relative_to(ROOT):raise ValueError('output must be outside checkout')
    output.mkdir(mode=0o700,parents=True,exist_ok=True)
    contents={}
    for project,target in [('transcription','asr'),('transcription-source','source')]:
        source=ROOT/'services'/project/'src'
        for path in sorted(source.rglob('*.py')):
            if '__pycache__' in path.parts:continue
            if path.is_symlink():raise ValueError('source symlinks forbidden')
            contents[target+'/'+path.relative_to(source).as_posix()]=path.read_bytes()
    for name in ('Dockerfile','requirements.lock','compose.yaml','provision.py','interview.py'):
        path=HERE/name
        if path.is_symlink():raise ValueError('source symlinks forbidden')
        contents[name]=path.read_bytes()
    contents['SHA256SUMS']=''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n'
        for name,data in sorted(contents.items())).encode()
    release=hashlib.sha256(contents['SHA256SUMS']).hexdigest()[:16]
    archive=output/('hr-transcription-'+release+'.tar.gz')
    sink=io.BytesIO()
    with gzip.GzipFile(fileobj=sink,mode='wb',mtime=0,filename='') as compressed:
        with tarfile.open(fileobj=compressed,mode='w') as tar:
            for name,data in sorted(contents.items()):
                item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o644;item.mtime=0
                tar.addfile(item,io.BytesIO(data))
    data=sink.getvalue()
    if archive.exists() and archive.read_bytes()!=data:raise ValueError('refuse overwrite')
    archive.write_bytes(data)
    return {'release':release,'archive':str(archive),'sha256':hashlib.sha256(data).hexdigest(),'files':len(contents)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    print(json.dumps(package(parser.parse_args().output)))
