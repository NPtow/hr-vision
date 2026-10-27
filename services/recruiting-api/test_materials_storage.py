import base64
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from materials_storage import FileSystemStorage, S3Storage, StorageError


class StorageTests(unittest.TestCase):
    def test_immutable_content_addressed_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = FileSystemStorage(Path(tmp) / 'private')
            source = Path(tmp) / 'source'; source.write_bytes(b'raw transcript')
            obj = storage.put_file(source, expected_size=14)
            self.assertEqual(storage.key_for_sha256(obj.sha256), obj.key)
            self.assertEqual(storage.connection_id, FileSystemStorage(Path(tmp) / 'private').connection_id)
            self.assertNotIn(tmp, storage.connection_id)
            self.assertEqual(obj.sha256, hashlib.sha256(b'raw transcript').hexdigest())
            self.assertEqual(storage.read_bytes(obj.key), b'raw transcript')
            self.assertEqual(storage.put_file(source).key, obj.key)
            self.assertEqual(obj.as_dict()['object_key'], obj.key)
            self.assertEqual((Path(tmp) / 'private' / obj.key).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(StorageError): storage.read_bytes(obj.key, max_bytes=2)
            with self.assertRaises(StorageError): storage.put_file(source, expected_sha256='0' * 64)
            with self.assertRaises(StorageError): storage.put_file(source, expected_size=999)

    def test_traversal_and_symlink_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'private'; storage = FileSystemStorage(root)
            source = Path(tmp) / 'source'; source.write_bytes(b'abc')
            for key in ('../secret', '/tmp/secret', 'sha256/xx/../secret', 'sha256\\bad'):
                with self.subTest(key=key), self.assertRaises(StorageError): storage.open(key)
            obj = storage.put_file(source)
            path = root / obj.key; path.write_bytes(b'bad')
            with self.assertRaises(StorageError): storage.put_file(source)
            path.unlink(); path.symlink_to(source)
            with self.assertRaises(StorageError): storage.open(obj.key)

    def test_s3_verified_metadata_checksum_and_conditional_write(self):
        class Client:
            def put_object(self, **kw):
                self.kw = kw; self.content = kw['Body'].read()
                self.head = {'ContentLength': len(self.content),
                             'Metadata': {'Sha256': kw['Metadata']['sha256']},
                             'ContentType': kw['ContentType']}
            def head_object(self, **kw): return self.head
            def get_object(self, **kw): return {'Body': io.BytesIO(self.content), **self.head}
            def generate_presigned_url(self, op, **kw): return 'https://bucket.example/signed'
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(); storage = S3Storage('private-bucket', client=client)
            source = Path(tmp) / 'source'; source.write_bytes(b'abc')
            obj = storage.put_file(source)
            self.assertEqual(client.kw['IfNoneMatch'], '*')
            self.assertIn('ChecksumSHA256', client.kw)
            self.assertEqual(client.kw['ContentMD5'],
                             base64.b64encode(hashlib.md5(b'abc', usedforsecurity=False).digest()).decode())
            self.assertNotIn('ACL', client.kw)
            self.assertEqual(storage.read_bytes(obj.key), b'abc')
            self.assertTrue(storage.signed_url(obj.key).startswith('https://'))
            client.head['Metadata'] = {'sha256': '0' * 64}
            with self.assertRaises(StorageError): storage.signed_url(obj.key)

    def test_s3_existing_key_checks_actual_bytes_and_connection_identity(self):
        from botocore.exceptions import ClientError
        class Client:
            def __init__(self): self.content = None
            def put_object(self, **kw):
                if self.content is not None:
                    raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
                self.content = kw['Body'].read(); self.metadata = kw['Metadata']
            def head_object(self, **kw): return {'ContentLength': len(self.content), 'Metadata': self.metadata}
            def get_object(self, **kw): return {'Body': io.BytesIO(self.content)}
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(); storage = S3Storage('private-bucket', client=client)
            source = Path(tmp) / 'source'; source.write_bytes(b'abc')
            obj = storage.put_file(source)
            self.assertEqual(storage.put_file(source).key, obj.key)
            self.assertEqual(storage.key_for_sha256(obj.sha256), obj.key)
            self.assertNotEqual(storage.connection_id, S3Storage('different-bucket', client=client).connection_id)
            client.content = b'bad'  # Metadata and length alone would miss corruption.
            with self.assertRaises(StorageError): storage.put_file(source)


if __name__ == '__main__':
    unittest.main()
