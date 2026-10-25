#!/usr/bin/env python3
"""Single-interview HTTP, worker, S3 and offline transcript acceptance."""
import argparse
import hashlib
import http.client
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

INTERVIEW = 'interview_75123c5506a524094e5c0c21'
# The installed helper lives outside the API checkout; imports must resolve
# against the release selected by the API service.
sys.path.insert(0, '/opt/hr-vision-api/current')


class Stop(Exception):
    pass


def need(ok, code):
    if not ok:
        raise Stop(code)


def db_open():
    path = os.environ.get('HR_MATERIALS_DB_PATH', '/var/lib/hr-vision-api/journey.sqlite3')
    need(path == '/var/lib/hr-vision-api/journey.sqlite3', 'unexpected_database')
    db = sqlite3.connect('file:' + path + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def preflight(db):
    env = os.environ
    need(env.get('HR_MATERIALS_ENABLED') == '1' and env.get('HR_MATERIALS_BACKFILL_ENABLED') == '0' and
         env.get('HR_MATERIALS_STORAGE_BACKEND') == 's3' and env.get('HR_MATERIALS_VIDEO_POLICY') == 'remote' and
         env.get('CELERY_BROKER_URL') and env.get('DAILY_API_KEY') and env.get('HR_TEAM_KEY'),
         'unsafe_configuration')
    row = db.execute('SELECT candidate_id,status,room FROM cp_interviews WHERE id=?', (INTERVIEW,)).fetchone()
    need(row and row['status'] == 'completed' and row['room'], 'interview_not_completed')
    room = json.loads(row['room'])
    need(room.get('provider', 'daily') == 'daily' and room.get('id') and room.get('name') == 'hrv-' + INTERVIEW
         and not room.get('captureNeverStarted'), 'unexpected_recorded_room')
    return row['candidate_id']


def rows(db, query, parameters):
    return [dict(row) for row in db.execute(query, parameters)]


def catalog(db):
    sources = rows(db, 'SELECT id,storage_policy,generation,state FROM media_sources WHERE interview_id=?', (INTERVIEW,))
    result = {'sources': sources, 'assets': [], 'locations': [], 'transcripts': [], 'jobs': [], 'attempts': []}
    if not sources:
        return result
    ids = [row['id'] for row in sources]
    slots = ','.join('?' for _ in ids)
    result['assets'] = rows(db, 'SELECT id,source_id,kind,state FROM media_assets WHERE source_id IN (' + slots + ')', ids)
    result['jobs'] = rows(db, 'SELECT id,source_id,generation,operation,status,attempts FROM media_jobs WHERE source_id IN (' + slots + ')', ids)
    asset_ids = [row['id'] for row in result['assets']]
    if asset_ids:
        slots = ','.join('?' for _ in asset_ids)
        result['locations'] = rows(db, 'SELECT asset_id,backend,object_key,state,size,sha256 FROM asset_locations WHERE asset_id IN (' + slots + ')', asset_ids)
        result['transcripts'] = rows(db, 'SELECT id,asset_id,version,raw_ref FROM transcript_versions WHERE asset_id IN (' + slots + ')', asset_ids)
    job_ids = [row['id'] for row in result['jobs']]
    if job_ids:
        slots = ','.join('?' for _ in job_ids)
        result['attempts'] = rows(db, 'SELECT job_id,status,worker_id FROM media_attempts WHERE job_id IN (' + slots + ')', job_ids)
    return result


def summary(data):
    videos = {row['id'] for row in data['assets'] if row['kind'] == 'video'}
    return {'source_count': len(data['sources']),
            'policies': sorted({row['storage_policy'] for row in data['sources']}),
            'video_states': sorted(row['state'] for row in data['assets'] if row['kind'] == 'video'),
            'owned_video_count': sum(row['asset_id'] in videos and row['backend'] == 's3' and row['state'] == 'verified'
                                     for row in data['locations']),
            'transcript_revisions': len(data['transcripts']),
            'jobs': {status: sum(row['status'] == status for row in data['jobs'])
                     for status in ('pending', 'retry', 'running', 'failed', 'succeeded')}}


def post(path, body, token=None):
    conn = http.client.HTTPConnection('127.0.0.1', 8391, timeout=15)
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        conn.request('POST', path, json.dumps(body).encode(), headers)
        reply = conn.getresponse()
        raw = reply.read(524289)
        need(reply.status == 200, 'http_' + str(reply.status))
        need(len(raw) <= 524288, 'http_response_too_large')
        return json.loads(raw)
    finally:
        conn.close()


def session():
    answer = post('/api/hr/session', {'scope': 'sfera', 'actor': 'manager', 'teamKey': os.environ['HR_TEAM_KEY']})
    token = answer.get('token')
    need(isinstance(token, str) and token, 'manager_session_missing')
    return token


def materials(candidate_id, token, policy=None):
    body = {'candidateId': candidate_id}
    if policy:
        body['storagePolicy'] = policy
    return post('/api/hr/recruiter/interview/materials', body, token)


def ready(data, policy):
    need(not any(row['status'] == 'failed' for row in data['jobs']), 'material_job_failed')
    need({row['storage_policy'] for row in data['sources']} <= {policy}, 'unexpected_storage_policy')
    videos = {row['id'] for row in data['assets'] if row['kind'] == 'video' and row['state'] == 'ready'}
    text_assets = {row['id'] for row in data['assets'] if row['kind'] == 'transcript'}
    owned_video = [row for row in data['locations'] if row['asset_id'] in videos and row['backend'] == 's3' and row['state'] == 'verified']
    remote_video = [row for row in data['locations'] if row['asset_id'] in videos and row['backend'] == 'provider' and row['state'] == 'available']
    owned_text = [row for row in data['transcripts'] if row['asset_id'] in text_assets and any(
        loc['asset_id'] == row['asset_id'] and loc['backend'] == 's3' and loc['state'] == 'verified'
        for loc in data['locations'])]
    successful = {row['job_id'] for row in data['attempts'] if row['status'] == 'succeeded' and row['worker_id'] == 'materials-local'}
    operations = {row['operation'] for row in data['jobs'] if row['id'] in successful and row['status'] == 'succeeded'}
    required = {'discover', 'import_transcript'} | ({'copy_asset'} if policy == 'owned' else set())
    done = bool(videos and remote_video and owned_text and required <= operations and
                (not owned_video if policy == 'remote' else owned_video))
    return done, owned_video, owned_text


def wait_ready(db, policy):
    deadline = time.monotonic() + 720
    while True:
        data = catalog(db)
        done, video, text = ready(data, policy)
        if done:
            return data, video, text
        need(time.monotonic() < deadline, 'material_wait_timeout')
        time.sleep(5)


def check_http(result, policy):
    videos = [row for row in result.get('recordings', []) if row.get('storage') == policy]
    texts = [row for row in result.get('transcripts', []) if row.get('storage') == 'owned' and
             isinstance(row.get('fullText'), str) and row['fullText'].strip()]
    need(result.get('speechProvider') == 'not_selected' and videos and texts and
         result.get('video', {}).get('status') == 'ready' and
         result.get('transcription', {}).get('status') == 'ready', 'http_materials_incomplete')
    return videos[0], texts[0]


def stream_hash(storage, key, expected_size, expected_sha256, max_bytes):
    need(type(expected_size) is int and 0 < expected_size <= max_bytes and
         isinstance(expected_sha256, str) and re.fullmatch('[0-9a-f]{64}', expected_sha256),
         'invalid_expected_digest')
    digest, count = hashlib.sha256(), 0
    body = storage.open(key)
    try:
        while chunk := body.read(1024 * 1024):
            count += len(chunk)
            need(count <= expected_size, 'object_size_mismatch')
            digest.update(chunk)
    finally:
        body.close()
    need(count == expected_size and digest.hexdigest() == expected_sha256, 'object_digest_mismatch')


def verify_raw_text(transcript, data, storage):
    raw = json.loads(transcript['raw_ref'])
    need(raw.get('backend') == 's3' and raw.get('object_key') and
         raw.get('object_key') == raw.get('key') and raw.get('sha256') and
         any(row['asset_id'] == transcript['asset_id'] and row['backend'] == 's3' and
             row['state'] == 'verified' and row['object_key'] == raw['object_key'] and
             row['size'] == raw.get('size') and row['sha256'] == raw['sha256']
             for row in data['locations']), 'raw_transcript_reference_mismatch')
    need(raw['object_key'] == storage.object_key(raw['sha256']), 'invalid_raw_transcript_key')
    raw_head = storage.head(raw['object_key'])
    need(raw_head.size == raw.get('size') and raw_head.sha256 == raw['sha256'], 'raw_transcript_head_mismatch')
    stream_hash(storage, raw['object_key'], raw['size'], raw['sha256'],
                int(os.environ.get('HR_MATERIALS_TRANSCRIPT_MAX_BYTES', str(20 * 1024**2))))


def verify_s3(video, transcript, data, storage):
    need(storage.backend == 's3' and video['size'] and re.fullmatch('[0-9a-f]{64}', video['sha256'] or '') and
         video['object_key'] == storage.object_key(video['sha256']), 'invalid_owned_video')
    stored = storage.head(video['object_key'])
    need(stored.size == video['size'] and stored.sha256 == video['sha256'], 's3_head_mismatch')
    stream_hash(storage, video['object_key'], video['size'], video['sha256'],
                int(os.environ.get('HR_MATERIALS_MAX_DOWNLOAD_BYTES', str(2 * 1024**3))))
    verify_raw_text(transcript, data, storage)


def verify_range(url):
    parsed = urlsplit(url)
    need(parsed.scheme == 'https' and parsed.netloc and parsed.query and not parsed.fragment, 'invalid_signed_url')
    for target, signed in ((url, True), (urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', '')), False)):
        request = urllib.request.Request(target, headers={'Range': 'bytes=0-63', 'Accept-Encoding': 'identity'})
        try:
            with urllib.request.urlopen(request, timeout=15) as answer:
                size = len(answer.read(65))
                need(signed and answer.status == 206 and 0 < size <= 64 and
                     answer.headers.get('Content-Range', '').startswith('bytes 0-'), 'signed_range_failed')
        except urllib.error.HTTPError as error:
            need(not signed and error.code == 403, 'public_access_not_denied')
        else:
            need(signed, 'public_access_not_denied')


def verify_offline_text(transcript_id, video_id=None):
    # An isolated process has no Daily credentials or capture adapter.
    code = ('from materials_runtime import build_runtime\n'
            'from materials_service import MaterialsService\n'
            'import os,sys\n'
            'r=build_runtime(captures={})\n'
            't=MaterialsService(r).get_transcript("interview",os.environ["ACCEPT_INTERVIEW"],os.environ["ACCEPT_TRANSCRIPT"])\n'
            'ok=bool(t.get("text", "").strip())\n'
            'if os.environ.get("ACCEPT_VIDEO"):\n'
            ' a=MaterialsService(r).get_asset_access("interview",os.environ["ACCEPT_INTERVIEW"],os.environ["ACCEPT_VIDEO"])\n'
            ' ok=ok and a.get("storage")=="owned" and a.get("url", "").startswith("https://")\n'
            'sys.exit(0 if ok else 1)\n')
    env = dict(os.environ, DAILY_API_KEY='', HR_VIDEO_ENABLED='0', ACCEPT_INTERVIEW=INTERVIEW,
               ACCEPT_TRANSCRIPT=transcript_id, ACCEPT_VIDEO=video_id or '')
    result = subprocess.run([sys.executable, '-c', code], env=env, cwd=os.getcwd(),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    need(result.returncode == 0, 'offline_material_unavailable')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('register-remote', 'assert-remote', 'upgrade-owned', 'assert-owned', 'status'))
    parser.add_argument('--interview-id', required=True)
    args = parser.parse_args()
    need(args.interview_id == INTERVIEW, 'interview_not_allowlisted')
    with db_open() as db:
        candidate_id = preflight(db)
        before = catalog(db)
        if args.phase == 'status':
            print(json.dumps({'phase': args.phase, 'result': summary(before)}, sort_keys=True))
            return
        if args.phase == 'register-remote':
            need(all(row['storage_policy'] == 'remote' for row in before['sources']), 'remote_downgrade_refused')
            need(materials(candidate_id, session(), 'remote').get('catalogVersion') == 1, 'materials_http_not_enabled')
        elif args.phase == 'assert-remote':
            data, _, texts = wait_ready(db, 'remote')
            video, text = check_http(materials(candidate_id, session()), 'remote')
            need(any(row['id'] == text['id'] for row in texts), 'transcript_revision_mismatch')
            from materials_runtime import build_runtime
            verify_raw_text(next(row for row in texts if row['id'] == text['id']), data, build_runtime().storage)
            verify_offline_text(text['id'])
        elif args.phase == 'upgrade-owned':
            done, _, _ = ready(before, 'remote')
            need(done, 'remote_phase_not_ready')
            token = session()
            check_http(materials(candidate_id, token, 'remote'), 'remote')
            need(materials(candidate_id, token, 'owned').get('catalogVersion') == 1, 'owned_upgrade_http_failed')
        else:
            data, owned, texts = wait_ready(db, 'owned')
            video, text = check_http(materials(candidate_id, session()), 'owned')
            need(any(row['id'] == video['id'] for row in data['assets'] if row['kind'] == 'video') and
                 any(row['id'] == text['id'] for row in texts), 'owned_revision_mismatch')
            selected = next(row for row in texts if row['id'] == text['id'])
            matching_locations = [row for row in owned if row['asset_id'] == video['id']]
            need(matching_locations, 'owned_video_location_mismatch')
            video_location = matching_locations[0]
            from materials_runtime import build_runtime
            verify_s3(video_location, selected, data, build_runtime().storage)
            verify_range(video['url'])
            verify_offline_text(text['id'], video['id'])
        print(json.dumps({'phase': args.phase, 'result': summary(catalog(db))}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Stop as error:
        print('Acceptance stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('Acceptance stopped: unexpected_error', file=sys.stderr)
        sys.exit(1)
