"""Content addressed input storage; no implicit deletion of shared blobs."""
from pathlib import Path
import hashlib
import os
import re
import tempfile

class Storage:
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve() / 'inputs'
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
            raise ValueError('invalid blob key')
        return self.root / key

    def put_file(self, path, sha256):
        destination = self.path(sha256)
        digest = hashlib.sha256()
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix='.stage-')
        try:
            with os.fdopen(fd, 'wb') as out, open(path, 'rb') as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if digest.hexdigest() != sha256:
                raise ValueError('input hash mismatch')
            os.replace(temporary, destination)
            directory = os.open(self.root, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        return sha256

    def verify(self, key, size, sha):
        path = self.path(key)
        digest = hashlib.sha256()
        observed = 0
        try:
            with path.open('rb') as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    observed += len(chunk)
                    if observed > size: return False
            return observed == size and digest.hexdigest() == sha
        except OSError:
            return False
