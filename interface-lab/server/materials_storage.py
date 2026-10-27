"""Private, content-addressed material objects. Authorization belongs to callers.

No public ACLs, overwrite, resume or deletion policy is implied by this module.
Configure a private bucket; signed URLs are temporary bearer capabilities.
"""
import base64
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat


class StorageError(ValueError):
    pass


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    size: int
    content_type: str = 'application/octet-stream'
    backend: str = 'filesystem'

    @property
    def object_key(self):
        return self.key

    def as_dict(self):
        result = asdict(self)
        result['object_key'] = result.pop('key')
        return result


def _expected(sha256, size, expected_sha256, expected_size):
    if expected_sha256 is not None and (not isinstance(expected_sha256, str) or
            not re.fullmatch('[a-fA-F0-9]{64}', expected_sha256) or sha256 != expected_sha256.lower()):
        raise StorageError('Material SHA-256 mismatch')
    if expected_size is not None and (type(expected_size) is not int or expected_size < 0 or size != expected_size):
        raise StorageError('Material size mismatch')


def _digest(handle):
    digest, size = hashlib.sha256(), 0
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _key(sha256):
    return f'sha256/{sha256[:2]}/{sha256}'


def _check_key(key, prefix=''):
    if not isinstance(key, str):
        raise StorageError('Invalid material key')
    match = re.fullmatch(re.escape(prefix) + r'sha256/([0-9a-f]{2})/([0-9a-f]{64})', key)
    if not match or match[1] != match[2][:2]:
        raise StorageError('Invalid material key')
    return match[2]


def _bounded_read(handle, key, max_bytes, prefix=''):
    if type(max_bytes) is not int or max_bytes <= 0:
        raise StorageError('Invalid read limit')
    data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise StorageError('Material exceeds read limit')
    if hashlib.sha256(data).hexdigest() != _check_key(key, prefix):
        raise StorageError('Stored material SHA-256 mismatch')
    return data


class FileSystemStorage:
    """Development/local store; keys never become filesystem/public URLs."""
    backend = 'filesystem'

    def __init__(self, root):
        root = Path(root).absolute()
        if root.is_symlink():
            raise StorageError('Storage root cannot be a symlink')
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = root.resolve()
        self.connection_id = 'filesystem:' + hashlib.sha256(str(self.root).encode()).hexdigest()

    def object_key(self, sha256):
        if not isinstance(sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', sha256):
            raise StorageError('Invalid material SHA-256')
        return _key(sha256)

    key_for_sha256 = object_key

    @contextmanager
    def _parent(self, key, create=False):
        _check_key(key)
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in key.split('/')[:-1]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor, key.split('/')[-1]
        except OSError as error:
            raise StorageError('Unsafe or unavailable material path') from error
        finally:
            os.close(descriptor)

    def open(self, key):
        with self._parent(key) as (parent, name):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    os.close(fd)
                    raise StorageError('Material is not a regular file')
                return os.fdopen(fd, 'rb')
            except OSError as error:
                raise StorageError('Unsafe or unavailable material path') from error

    def read_bytes(self, key, max_bytes=16 * 1024 * 1024):
        with self.open(key) as handle:
            return _bounded_read(handle, key, max_bytes)

    def head(self, key):
        with self.open(key) as handle:
            digest, size = _digest(handle)
        if digest != _check_key(key):
            raise StorageError('Stored material SHA-256 mismatch')
        return StoredObject(key, digest, size)

    def put_file(self, path, *, expected_sha256=None, expected_size=None, content_type='application/octet-stream'):
        # Stage privately before publishing; a crash cannot expose a partial object.
        stage = self.root / ('.stage-' + secrets.token_hex(16))
        try:
            fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as target, open(path, 'rb') as source:
                digest, size = hashlib.sha256(), 0
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                target.flush()
                os.fsync(target.fileno())
            digest = digest.hexdigest()
            _expected(digest, size, expected_sha256, expected_size)
            key = self.object_key(digest)
            with self._parent(key, create=True) as (parent, name):
                try:
                    os.link(stage, name, dst_dir_fd=parent, follow_symlinks=False)
                    os.fsync(parent)
                except FileExistsError:
                    existing = self.head(key)
                    _expected(existing.sha256, existing.size, digest, size)
            return StoredObject(key, digest, size, content_type)
        except OSError as error:
            raise StorageError('Could not store material') from error
        finally:
            stage.unlink(missing_ok=True)

    def signed_url(self, key, expires_seconds=300):
        _check_key(key)
        raise StorageError('Local material delivery is unavailable; no public file URL')


class S3Storage:
    """Single-PUT immutable objects (max 5 GiB), with service SHA-256 checking.

    Boto3 obtains credentials from its standard chain; never accepts them from
    material URLs. Bucket privacy/lifecycle/versioning are deployment settings.
    """
    backend = 's3'

    def __init__(self, bucket, *, prefix='materials', client=None, endpoint_url=None, region_name=None):
        if not isinstance(bucket, str) or not bucket or not re.fullmatch(r'[a-zA-Z0-9._-]+', bucket):
            raise StorageError('Invalid bucket')
        if not isinstance(prefix, str) or not re.fullmatch(r'[a-zA-Z0-9_/-]+', prefix) or any(part in ('', '.', '..') for part in prefix.split('/')):
            raise StorageError('Invalid storage prefix')
        self.bucket, self.prefix = bucket, prefix + '/'
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client('s3', endpoint_url=endpoint_url, region_name=region_name,
                                  config=Config(signature_version='s3v4', connect_timeout=10, read_timeout=60,
                                                retries={'mode': 'standard', 'total_max_attempts': 3}))
        self.client = client
        endpoint_identity = endpoint_url or getattr(getattr(client, 'meta', None), 'endpoint_url', '')
        identity = json.dumps([self.backend, bucket, self.prefix, endpoint_identity], separators=(',', ':'))
        self.connection_id = 's3:' + hashlib.sha256(identity.encode()).hexdigest()

    def object_key(self, sha256):
        if not isinstance(sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', sha256):
            raise StorageError('Invalid material SHA-256')
        return self.prefix + _key(sha256)

    key_for_sha256 = object_key

    def head(self, key):
        digest = _check_key(key, self.prefix)
        response = self.client.head_object(Bucket=self.bucket, Key=key)
        size = response.get('ContentLength')
        if response.get('Metadata', {}).get('sha256') != digest or type(size) is not int or size < 0:
            raise StorageError('Stored object verification failed')
        checksum = response.get('ChecksumSHA256')
        if checksum is not None and checksum != base64.b64encode(bytes.fromhex(digest)).decode('ascii'):
            raise StorageError('Stored object checksum mismatch')
        return StoredObject(key, digest, size, response.get('ContentType', 'application/octet-stream'), self.backend)

    def put_file(self, path, *, expected_sha256=None, expected_size=None, content_type='application/octet-stream'):
        from botocore.exceptions import ClientError
        with open(path, 'rb') as source:
            digest, size = _digest(source)
            _expected(digest, size, expected_sha256, expected_size)
            if size > 5 * 1024 ** 3:
                raise StorageError('Material exceeds single-upload limit')
            key = self.object_key(digest)
            source.seek(0)
            try:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=source, ContentLength=size,
                                       ContentType=content_type, Metadata={'sha256': digest}, IfNoneMatch='*',
                                       ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode('ascii'))
            except ClientError as error:
                if error.response.get('Error', {}).get('Code') not in ('PreconditionFailed', '412'):
                    raise
                # A colliding pre-existing key must contain the same bytes;
                # user-defined metadata alone cannot prove this.
                body = self.client.get_object(Bucket=self.bucket, Key=key)['Body']
                try:
                    actual_digest, actual_size = _digest(body)
                finally:
                    body.close()
                _expected(actual_digest, actual_size, digest, size)
            verified = self.head(key)
            _expected(verified.sha256, verified.size, digest, size)
            return verified

    def open(self, key):
        self.head(key)
        return self.client.get_object(Bucket=self.bucket, Key=key)['Body']

    def read_bytes(self, key, max_bytes=16 * 1024 * 1024):
        with self.open(key) as handle:
            return _bounded_read(handle, key, max_bytes, self.prefix)

    def signed_url(self, key, expires_seconds=300):
        self.head(key)
        if type(expires_seconds) is not int or not 1 <= expires_seconds <= 3600:
            raise StorageError('Invalid signed URL lifetime')
        return self.client.generate_presigned_url('get_object', Params={'Bucket': self.bucket, 'Key': key},
                                                  ExpiresIn=expires_seconds, HttpMethod='GET')
