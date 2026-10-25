#!/usr/bin/env python3
"""Push already verified engine receipts through the same authenticated staff API.

The input contains candidate/interview IDs, the exact prepared request and the
sealed candidate_profile returned by the engine. Never invokes a model, never
publishes a shortlist or sends messages. Secrets come from the running API's
environment on the server, or HR_TEAM_KEY for an isolated local test.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('file', type=Path)
    p.add_argument('--api', default='http://127.0.0.1:8391')
    p.add_argument('--from-unit', action='store_true')
    args = p.parse_args()
    env = os.environ
    if args.from_unit:
        pid = subprocess.check_output(['systemctl', 'show', 'hr-vision-api', '-p', 'MainPID', '--value'], text=True).strip()
        env = dict(v.split('=', 1) for v in Path('/proc/' + pid + '/environ').read_text().split('\0') if '=' in v)
    key = env['HR_TEAM_KEY']
    def call(path, body=None, token=None):
        headers = {'Content-Type': 'application/json', 'Origin': env.get('HR_ORIGIN', 'https://hr-vision.158-160-179-53.sslip.io')}
        if token: headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(args.api.rstrip('/') + '/api/hr/' + path,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as reply: return json.load(reply)
        except urllib.error.HTTPError as error:
            # No raw response body, contact, token or input text in diagnostics.
            raise RuntimeError('Selection API refused ' + path.split('?')[0] + ': HTTP ' + str(error.code)) from None
    token = call('session', {'teamKey': key, 'actor': 'manager'})['token']
    data = json.loads(args.file.read_text())
    before = {v['id']: v for v in call('selection/candidates', token=token)['candidates']}
    receipts = []
    for item in data['results']:
        cid, request, doc = item['candidateId'], item['request'], item['document']
        person = before[cid]
        if person['interviewId'] != item['interviewId'] or request['candidate_id'] != doc['body']['candidate_id']:
            raise RuntimeError('Candidate/interview identity mismatch')
        if request['provenance']['synthetic'] is not False:
            raise RuntimeError('Synthetic request is not a real candidate assessment')
        for source in request['sources']:
            if hashlib.sha256(source['text'].encode()).hexdigest() != source['sha256']:
                raise RuntimeError('Input text checksum mismatch')
        request_hash = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        saved = call('selection/results', {'candidateId': cid, 'interviewId': item['interviewId'],
            'engineCandidateId': request['candidate_id'], 'jobId': item['jobId'], 'requestHash': request_hash,
            'sourceRevision': person['sourceRevision'], 'document': doc}, token)
        receipts.append({'candidateId': cid, 'resultId': saved['id'], 'existing': saved['existing'],
                         'documentHash': doc['document_sha256'], 'jobId': item['jobId']})
    if data.get('candidateIds'):
        call('selection/cohort', {'vacancyId': data['vacancyId'], 'candidateIds': data['candidateIds']}, token)
    after = call('selection/candidates?vacancyId=' + data['vacancyId'], token=token)
    print(json.dumps({'results': receipts, 'cohort': [{
        'candidateId': v['id'], 'assessment': v['assessment']['recommendation'] if v['assessment'] else None,
        'canAdd': v['canAdd'], 'application': v['application'], 'nomination': v['nomination']}
        for v in after['candidates'] if v['inCohort']]}, ensure_ascii=False))


if __name__ == '__main__': main()
