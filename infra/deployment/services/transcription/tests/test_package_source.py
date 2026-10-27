"""Release archive carries independently importable ingress/scheduler modules."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tarfile

PACKAGE=Path(__file__).parents[1]/'package.py'
spec=importlib.util.spec_from_file_location('transcription_package',PACKAGE)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_source_ingress_archive_is_complete_deterministic_and_importable(tmp_path):
    first=module.package(tmp_path/'out')
    second=module.package(tmp_path/'out')
    assert first==second
    target=tmp_path/'unpacked'
    target.mkdir()
    with tarfile.open(first['archive']) as archive:
        members={entry.name:archive.extractfile(entry).read() for entry in archive.getmembers()}
    for name in ('catalogue.py','uploads.py','upload_repository.py','api.py','repository.py'):
        assert 'source/hr_transcription_source/'+name in members
    for line in members['SHA256SUMS'].decode().splitlines():
        digest,name=line.split('  ',1)
        assert hashlib.sha256(members[name]).hexdigest()==digest
    # Extract only the known source subtree; no HR app or source-tree path leaks
    # may be required merely to import the independently deployed API.
    for name,data in members.items():
        if name.startswith('source/'):
            path=target/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(data)
    result=subprocess.run([sys.executable,'-c',
        "from hr_transcription_source.api import Recording,UploadBegin; "
        "from hr_transcription_source.catalogue import Catalogue; "
        "assert 'intake' in Recording.model_json_schema()['properties']['owner_kind']['enum']; "
        "assert 'files' in UploadBegin.model_fields"],cwd=target,
        env={**os.environ,'PYTHONPATH':str(target/'source'),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
    assert result.returncode==0,result.stderr
