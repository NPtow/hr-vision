"""Example for a call-source service; it alone later submits the result to core."""
import argparse
import json
import os
from pathlib import Path
import time
import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('file', type=Path)
    parser.add_argument('--request-id', required=True)
    parser.add_argument('--language')
    args = parser.parse_args()
    base = os.environ['TRANSCRIPTION_URL'].rstrip('/')
    token = os.environ['TRANSCRIPTION_TOKEN']
    session = requests.Session()
    session.trust_env = False
    session.headers['Authorization'] = 'Bearer ' + token
    with args.file.open('rb') as stream:
        response = session.post(base + '/v1/jobs', params={'language': args.language} if args.language else {},
            data=stream, headers={'X-Filename': args.file.name, 'Idempotency-Key': args.request_id,
                                  'Content-Type': 'application/octet-stream'},
            timeout=(5, 900), allow_redirects=False)
    response.raise_for_status()
    identity = response.json()['id']
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        response = session.get(base + '/v1/jobs/' + identity, timeout=(5, 30), allow_redirects=False)
        response.raise_for_status()
        job = response.json()
        if job['status'] == 'succeeded':
            result = session.get(base + '/v1/jobs/' + identity + '/result', timeout=(5, 30), allow_redirects=False)
            result.raise_for_status()
            print(json.dumps(result.json(), ensure_ascii=False))
            return
        if job['status'] in ('failed', 'uncertain'):
            raise SystemExit('Job ' + identity + ': ' + job['status'] + ' (' + (job['error_code'] or '') + ')')
        time.sleep(3)
    raise SystemExit('Polling deadline reached; retry polling the same job ' + identity)


if __name__ == '__main__':
    main()
