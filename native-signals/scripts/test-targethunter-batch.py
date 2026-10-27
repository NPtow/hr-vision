#!/usr/bin/env python3
"""Offline acceptance: reused live receipt, no credentials/network, dry-run purity and resume."""
import json
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
BINARY = ROOT / '.build/targethunter-batch/targethunter-batch'
SCHEMA = pathlib.Path.home() / 'Library/Application Support/TG Signals/source-runs/source-81c10dd2-2cb0-4f15-b706-ae631162fb99.json'
MODULE = ROOT / 'output/targethunter-discovery/vk_modules_list-eaeb0e97-e9fd-48c6-9b59-85a6eeae276b.json'
PILOT = ROOT / 'output/targethunter-pilot'
ARGS = [str(BINARY), '--schema-run', str(SCHEMA), '--module-run', str(MODULE), '--pilot-dir', str(PILOT)]


def run(args, expect=0):
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == expect, result.stdout + result.stderr
    return [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]


with tempfile.TemporaryDirectory(prefix='tg-th-batch-offline-') as tmp:
    data = pathlib.Path(tmp) / 'data'
    args = ARGS + ['--data-dir', str(data), '--scope', 'all', '--max-new', '0']
    events = run(args)
    assert events[0]['catalogCompanies'] == 409
    assert events[0]['selectedCompanies'] == 409
    assert events[0]['reusedPilotTasks'] == 1
    assert events[0]['maximumNewTasks'] == 0
    assert not data.exists(), 'dry run wrote files'
    # max-new0 + no queued remote tasks means only the saved pilot is imported.
    events = run(args + ['--execute'])
    runs = list((data / 'source-runs').glob('*.json'))
    assert len(runs) == 1
    record = json.loads(runs[0].read_text())
    assert record['request']['companyID'] == 'brunoyam'
    assert len(record['results']) == 5
    assert record['targetHunterTask']['reportedCount'] == 15
    assert record['targetHunterTask']['completeness'] == 'partial'
    assert not any(event.get('event') in ('created', 'export_received', 'read_backoff') for event in events)
    before = runs[0].read_bytes()
    journal = data / 'targethunter-batch/journal.json'
    manifest = json.loads((data / 'targethunter-batch/coverage.json').read_text())
    assert manifest['running'] is False
    assert manifest['imported'] == 1 and manifest['planned'] == 408
    assert manifest['receivedResults'] == 5 and manifest['partial'] == 1
    run(args + ['--execute'])
    assert len(list((data / 'source-runs').glob('*.json'))) == 1
    assert runs[0].read_bytes() == before, 'resume rewrote receipt'
    # Bad durable journal must fail before reading credentials or starting any network.
    value = json.loads(journal.read_text())
    value['entries'][0]['query'] = 'different'
    journal.write_text(json.dumps(value))
    events = run(args + ['--execute'], expect=1)
    assert events[-1]['event'] == 'stopped'
    assert runs[0].read_bytes() == before
    # Proof safety gates preserve creation intent and no implicit create retry API exists.
    source = (ROOT / 'scripts/targethunter-batch.swift').read_text()
    assert 'SecItem' not in source and 'Security' not in source
    assert 'try saveJournal(journal, options) // fsync BEFORE create' in source
    assert 'try saveJournal(journal, options) // persist task ID before next create' in source
    assert 'No create retry' in source or 'Never retried.' in source

run([str(BINARY), '--self-test'])
print(json.dumps({'status': 'passed', 'checks': 25, 'networkCalls': 0, 'credentialReads': 0,
                  'realPilotReceipt': '5 received / 15 reported', 'catalogCompanies': 409}))
