#!/usr/bin/env python3
"""One-shot live S3 compatibility/privacy probe. Creates tiny objects; never deletes.

Run on the server with the materials venv after /etc/hr-vision/materials.env is
configured. No credential, request URL, response body, or exception text is printed.
Normal writes are confined to materials/probes/<random-id>/. One conditional
negative test targets a random key outside that prefix; it should be denied
before writing and fails the probe if accepted.
"""

import argparse
import base64
import hashlib
import os
from pathlib import Path
import secrets
import shlex
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit


def need(condition, code):
    if not condition:
        raise ProbeFailure(code)


class ProbeFailure(Exception):
    pass


def load_env(path):
    need(path.is_file() and not path.is_symlink(), 'environment_file_unavailable')
    for raw in path.read_text().splitlines():
        parts = shlex.split(raw, comments=True)
        if not parts:
            continue
        need(len(parts) == 1 and '=' in parts[0], 'environment_file_invalid')
        name, value = parts[0].split('=', 1)
        need(name.isidentifier(), 'environment_file_invalid')
        os.environ[name] = value


def status_of(error):
    return error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')


def http_get(url, *, signed):
    request = urllib.request.Request(url, headers={'Range': 'bytes=0-63'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(65)
            return response.status, response.headers.get('Content-Range'), body
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, None, None
    except Exception:
        raise ProbeFailure('signed_http_unavailable' if signed else 'unsigned_http_unavailable') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('/etc/hr-vision/materials.env'))
    parser.add_argument('--outside-canary-key',
                        help='Existing admin-created object outside materials/; only GET is attempted')
    args = parser.parse_args()
    load_env(args.env_file)
    env = os.environ
    need(env.get('HR_MATERIALS_STORAGE_BACKEND') == 's3', 's3_backend_not_configured')
    need(env.get('HR_MATERIALS_S3_PREFIX', 'materials') == 'materials', 'unexpected_storage_prefix')
    bucket = env.get('HR_MATERIALS_S3_BUCKET')
    endpoint = env.get('HR_MATERIALS_S3_ENDPOINT')
    region = env.get('HR_MATERIALS_S3_REGION')
    need(bucket and endpoint and region, 's3_configuration_incomplete')
    parsed = urlsplit(endpoint)
    need(parsed.scheme == 'https' and parsed.hostname and not parsed.username and
         not parsed.password and not parsed.query and not parsed.fragment,
         'unsafe_s3_endpoint')

    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    client = boto3.client('s3', endpoint_url=endpoint, region_name=region,
                          config=Config(signature_version='s3v4', connect_timeout=10,
                                        read_timeout=30, retries={'mode': 'standard',
                                                                  'total_max_attempts': 2}))
    key = 'materials/probes/' + secrets.token_hex(16) + '/payload'
    body = secrets.token_bytes(256 * 1024)
    digest = hashlib.sha256(body).digest()
    digest_hex = digest.hex()
    checksum = base64.b64encode(digest).decode('ascii')
    common = dict(Bucket=bucket, ContentType='application/octet-stream',
                  Metadata={'sha256': digest_hex}, IfNoneMatch='*')

    # Match materials_storage.S3Storage.put_file's conditional, checksum-bearing PUT.
    client.put_object(Key=key, Body=body, ContentLength=len(body),
                      ChecksumSHA256=checksum, **common)
    print('correct_checksum_put: PASS')

    head = client.head_object(Bucket=bucket, Key=key)
    need(head.get('ContentLength') == len(body) and
         head.get('Metadata', {}).get('sha256') == digest_hex,
         'head_metadata_or_size_mismatch')
    service_checksum = head.get('ChecksumSHA256')
    need(service_checksum is None or service_checksum == checksum, 'head_checksum_mismatch')
    print('head_metadata_size: PASS')

    result = client.get_object(Bucket=bucket, Key=key)
    stream = result['Body']
    try:
        actual = stream.read(len(body) + 1)
    finally:
        stream.close()
    need(len(actual) == len(body) and hashlib.sha256(actual).digest() == digest,
         'get_sha256_mismatch')
    print('get_sha256: PASS')

    wrong = base64.b64encode(bytes([digest[0] ^ 1]) + digest[1:]).decode('ascii')
    try:
        client.put_object(Key=key + '-wrong-checksum', Body=body, ContentLength=len(body),
                          ChecksumSHA256=wrong, **common)
    except ClientError as error:
        need(status_of(error) in (400, 422), 'wrong_checksum_unexpected_status')
    else:
        raise ProbeFailure('wrong_checksum_accepted')
    print('wrong_checksum_rejected: PASS')

    changed = b'different immutable content'
    changed_checksum = base64.b64encode(hashlib.sha256(changed).digest()).decode('ascii')
    try:
        client.put_object(Key=key, Body=changed, ContentLength=len(changed),
                          ChecksumSHA256=changed_checksum, **common)
    except ClientError as error:
        need(status_of(error) == 412, 'overwrite_unexpected_status')
    else:
        raise ProbeFailure('overwrite_accepted')
    print('overwrite_412: PASS')

    signed_url = client.generate_presigned_url('get_object',
        Params={'Bucket': bucket, 'Key': key}, ExpiresIn=300, HttpMethod='GET')
    signed_parts = urlsplit(signed_url)
    need(signed_parts.scheme == 'https' and 'X-Amz-Signature=' in signed_parts.query,
         'signed_url_invalid')
    status, content_range, data = http_get(signed_url, signed=True)
    need(status == 206 and content_range == f'bytes 0-63/{len(body)}' and data == body[:64],
         'signed_range_failed')
    print('signed_range_206: PASS')

    unsigned_url = urlunsplit((signed_parts.scheme, signed_parts.netloc, signed_parts.path, '', ''))
    status, _, _ = http_get(unsigned_url, signed=False)
    need(status == 403, 'unsigned_get_not_403')
    print('unsigned_get_403: PASS')

    # A nonexistent random key makes DeleteObject a permission probe without
    # deleting a real object. uploader must not have delete permission.
    try:
        client.delete_object(Bucket=bucket, Key=key + '-never-created-' + secrets.token_hex(8))
    except ClientError as error:
        need(status_of(error) == 403, 'delete_unexpected_status')
    else:
        raise ProbeFailure('delete_permission_present')
    print('delete_denied_403: PASS')

    # First prove this endpoint honors If-Match with an authorized key. Then
    # apply the same impossible condition to a fresh key outside materials/:
    # 403 means prefix denied; 412 would mean the PUT was authorized. Even if a
    # faulty endpoint ignores If-Match, only a synthetic random key is written.
    impossible_etag = '"00000000000000000000000000000000"'
    try:
        client.put_object(Key=key, Body=changed, ContentLength=len(changed),
                          ChecksumSHA256=changed_checksum, IfMatch=impossible_etag,
                          Bucket=bucket, ContentType='application/octet-stream')
    except ClientError as error:
        need(status_of(error) == 412, 'if_match_precondition_unexpected_status')
    else:
        raise ProbeFailure('if_match_precondition_ignored')
    print('if_match_precondition_412: PASS')

    outside_key = 'materials-prefix-denied-probe/' + secrets.token_hex(16) + '/payload'
    try:
        client.put_object(Key=outside_key, Body=changed, ContentLength=len(changed),
                          ChecksumSHA256=changed_checksum, IfMatch=impossible_etag,
                          Bucket=bucket, ContentType='application/octet-stream')
    except ClientError as error:
        need(status_of(error) == 403, 'outside_prefix_put_not_403')
    else:
        raise ProbeFailure('outside_prefix_put_accepted')
    print('outside_prefix_put_denied_403: PASS')

    if args.outside_canary_key:
        need(not args.outside_canary_key.startswith('materials/') and
             '/' in args.outside_canary_key and '..' not in args.outside_canary_key.split('/'),
             'outside_canary_key_invalid')
        try:
            response = client.get_object(Bucket=bucket, Key=args.outside_canary_key)
        except ClientError as error:
            need(status_of(error) == 403, 'outside_prefix_get_not_403')
        else:
            response['Body'].close()
            raise ProbeFailure('outside_prefix_get_accepted')
        print('outside_prefix_get_denied_403: PASS')
    print('PROBE_PASS (created test objects retained under materials/probes)')


if __name__ == '__main__':
    try:
        main()
    except ProbeFailure as error:
        print('PROBE_FAIL ' + str(error), file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        # SDK exceptions can contain a presigned URL, endpoint, or response data.
        print('PROBE_FAIL unexpected_' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
