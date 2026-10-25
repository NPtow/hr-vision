#!/usr/bin/env python3
"""Root-owned, fixed-purpose SSH receiver. Never executes uploaded build scripts."""
import fcntl
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

REPO = 'NPtow/hr-vision'
BRANCHES = {'service': 'main', 'mockups': 'codex/mockups'}
BASES = {'service': Path('/srv/hr-vision'), 'mockups': Path('/srv/hr-vision-mockups')}
API = Path('/opt/hr-vision-api')
DATABASE = Path('/var/lib/hr-vision-api/journey.sqlite3')
PRE_IMPORT_RUNTIME = {
    'app.py', 'browser_access.py', 'candidate_portal.py', 'candidate_resume.py', 'candidate_notifications.py', 'candidate_bot.py',
    'candidate_invites.py', 'recruiter_availability.py', 'hh_recruiting.py', 'hh_routes.py',
    'recruiter_access.py', 'recruiter_sources.py', 'recruiter_notifications.py',
    'employer_access.py', 'employer_store.py', 'employer_workflow.py', 'employer_routes.py',
    'employer_projection.py', 'employer_intake.py', 'daily_media.py', 'domain.py', 'dsa_panel.py', 'feedback.py',
}
# Keep complete previous archives accepted. Activation also checks durable data
# compatibility; an accepted layout is not by itself a safe rollback target.
PRE_OUTREACH_RUNTIME = PRE_IMPORT_RUNTIME | {'vacancy_import.py', 'vacancy_sources.py'}
OUTREACH_RUNTIME = {
    'candidate_booking.py', 'hh_outreach.py', 'hh_outreach_routes.py', 'hh_outreach_worker.py',
    'hh_browser/__init__.py', 'hh_browser/adapter.py', 'hh_browser/store.py', 'hh_browser/bridge.py',
}
PRE_ACCESS_RUNTIME = PRE_OUTREACH_RUNTIME | OUTREACH_RUNTIME
PRE_CV_RUNTIME = PRE_ACCESS_RUNTIME | {'candidate_access.py'}
PRE_POOLED_RUNTIME = PRE_CV_RUNTIME | {'structured_model.py', 'candidate_resume_model.py', 'candidate_resume_prompts.py'}
PRE_EMPLOYER_TELEGRAM_RUNTIME = PRE_POOLED_RUNTIME | {'interview_scheduler.py'}
PRE_REMINDERS_RUNTIME = PRE_EMPLOYER_TELEGRAM_RUNTIME | {'employer_notifications.py', 'telegram_miniapp.py'}
PRE_MEETING_NOTICES_RUNTIME = PRE_REMINDERS_RUNTIME | {'meeting_reminders.py'}
PRE_MEETING_LAB_RUNTIME = PRE_MEETING_NOTICES_RUNTIME | {'meeting_notifications.py'}
PRE_RECRUITER_COACH_RUNTIME = PRE_MEETING_LAB_RUNTIME | {'meeting_lab.py'}
PRE_CALL_CLEANUP_RUNTIME = PRE_RECRUITER_COACH_RUNTIME | {'recruiter_coach.py'}
PRE_OBSERVABILITY_RUNTIME = PRE_CALL_CLEANUP_RUNTIME | {'call_cleanup.py', 'media_runtime.py'}
PRE_CANDIDATE_CALENDAR_RUNTIME = PRE_OBSERVABILITY_RUNTIME | {'observability.py'}
PRE_INTERVIEW_ASSISTANT_RUNTIME = PRE_CANDIDATE_CALENDAR_RUNTIME | {'candidate_availability.py'}
PRE_MATERIALS_RUNTIME = PRE_INTERVIEW_ASSISTANT_RUNTIME | {'interview_assistant.py'}
MATERIALS_RUNTIME = {
    'materials_capture.py', 'materials_daily.py', 'materials_repository.py',
    'materials_http.py', 'materials_storage.py', 'materials_transcripts.py',
    'materials_worker.py', 'materials_runtime.py', 'materials_service.py',
}
PRE_SERVICES_RUNTIME = PRE_MATERIALS_RUNTIME | MATERIALS_RUNTIME
SERVICES_RUNTIME = {'core_source_client.py', 'core_source_outbox.py', 'zoom_api_client.py'}
PRE_ZOOM_RUNTIME = PRE_SERVICES_RUNTIME
ZOOM_RUNTIME = {'call_providers.py', 'zoom_media.py', 'materials_zoom.py', 'zoom_recording_retention.py'}
SERVICES_ONLY_RUNTIME = PRE_SERVICES_RUNTIME | SERVICES_RUNTIME
ZOOM_ONLY_RUNTIME = PRE_ZOOM_RUNTIME | ZOOM_RUNTIME
# Preserve both independently shipped complete histories. New releases include
# the main core/source service boundary and the approved assessment capability.
ASSESSMENT_ONLY_RUNTIME = ZOOM_ONLY_RUNTIME | {'interview_assessment.py'}
PRE_ASSESSMENT_RUNTIME = SERVICES_ONLY_RUNTIME | ZOOM_RUNTIME
PRE_PUBLIC_PROFILE_RUNTIME = PRE_ASSESSMENT_RUNTIME | {'interview_assessment.py'}
PRE_SELECTION_RUNTIME = PRE_PUBLIC_PROFILE_RUNTIME | {'public_profiles.py'}
PRE_JEV_RUNTIME = PRE_SELECTION_RUNTIME | {'selection.py'}
PRE_ASR_DISPATCH_RUNTIME = PRE_JEV_RUNTIME | {'assessment_jobs.py'}
PRE_PRODUCT_RECORDINGS_RUNTIME = PRE_ASR_DISPATCH_RUNTIME | {'materials_asr_dispatch.py'}
RUNTIME = PRE_PRODUCT_RECORDINGS_RUNTIME | {'recording_uploads.py', 'canonical_materials.py', 'transcript_bundle.py'}
BOT_RUNTIME = PRE_IMPORT_RUNTIME - {'recruiter_access.py', 'recruiter_sources.py', 'recruiter_notifications.py'}
LEGACY_RUNTIME = BOT_RUNTIME - {'candidate_bot.py'}
RUNTIME_LAYOUTS = {frozenset(PRE_PRODUCT_RECORDINGS_RUNTIME), frozenset(PRE_JEV_RUNTIME), frozenset(LEGACY_RUNTIME), frozenset(BOT_RUNTIME), frozenset(PRE_IMPORT_RUNTIME),
                   frozenset(PRE_SERVICES_RUNTIME), frozenset(SERVICES_ONLY_RUNTIME), frozenset(ZOOM_ONLY_RUNTIME),
                   frozenset(ASSESSMENT_ONLY_RUNTIME), frozenset(PRE_ASSESSMENT_RUNTIME), frozenset(PRE_PUBLIC_PROFILE_RUNTIME), frozenset(PRE_SELECTION_RUNTIME), frozenset(PRE_ASR_DISPATCH_RUNTIME),
                   frozenset(PRE_OUTREACH_RUNTIME), frozenset(PRE_ACCESS_RUNTIME), frozenset(PRE_CV_RUNTIME),
                   frozenset(PRE_POOLED_RUNTIME), frozenset(PRE_EMPLOYER_TELEGRAM_RUNTIME), frozenset(PRE_REMINDERS_RUNTIME), frozenset(PRE_MEETING_NOTICES_RUNTIME), frozenset(PRE_MEETING_LAB_RUNTIME), frozenset(PRE_RECRUITER_COACH_RUNTIME), frozenset(PRE_CALL_CLEANUP_RUNTIME), frozenset(PRE_OBSERVABILITY_RUNTIME), frozenset(PRE_CANDIDATE_CALENDAR_RUNTIME), frozenset(PRE_INTERVIEW_ASSISTANT_RUNTIME), frozenset(PRE_MATERIALS_RUNTIME), frozenset(RUNTIME)}
MAX_ARCHIVE = 50 * 1024 * 1024
MAX_CONTENT = 120 * 1024 * 1024


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unpack(archive, destination, target):
    """Validate every member and digest before writing any payload to disk."""
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        require(len(members) <= 5000, 'Too many archive entries')
        names = set()
        total = 0
        files = {}
        for member in members:
            name = member.name.rstrip('/')
            path = PurePosixPath(name)
            require(name and str(path) == name and not path.is_absolute() and '..' not in path.parts and '\\' not in name, 'Unsafe archive path')
            require(name not in names, 'Duplicate archive member')
            names.add(name)
            require(member.isdir() or member.isfile(), 'Links and special files are forbidden')
            require(path.parts[0] in {'ui', 'api', 'release.json'}, 'Unknown bundle entry')
            if member.isdir():
                require(path.parts[0] in {'ui', 'api'}, 'Invalid directory')
                continue
            total += member.size
            require(total <= MAX_CONTENT, 'Expanded archive too large')
            files[name] = bundle.extractfile(member).read()
        manifest = json.loads(files.pop('release.json', b'{}'))
        require(manifest.get('version') == 1 and manifest.get('target') == target, 'Wrong deployment target')
        require(manifest.get('branch') == BRANCHES[target], 'Wrong source branch')
        require(re.fullmatch('[a-f0-9]{40}', manifest.get('sha', '')), 'Invalid source commit')
        require(re.fullmatch(r'\d+\.\d+', manifest.get('run', '')), 'Invalid workflow run')
        require(manifest.get('release') == manifest['sha'] + '-' + manifest['run'], 'Invalid release identity')
        require(set(files) == set(manifest.get('files', {})), 'File manifest differs from payload')
        for name, data in files.items():
            require(digest(data) == manifest['files'][name], 'File checksum mismatch: ' + name)
            if name.startswith('api/'):
                require(target == 'service' and name[4:] in RUNTIME, 'Unexpected API file')
            else:
                rel = name.removeprefix('ui/')
                require(name.startswith('ui/') and not any(part.startswith('.') for part in PurePosixPath(rel).parts), 'Unexpected public path')
                require(re.search(r'\.(js|css|svg|woff2?|ttf|png|jpe?g|webp|ico|gif)$', rel)
                        or rel in {'index.html', 'iframe.html', 'index.json', 'project.json', 'deployment.json'}
                        or rel in {'call/zoom/client.html', 'call/zoom/left.html'}
                        or (target == 'mockups' and rel == 'presentations/hrds-2026-10-07/index.html')
                        or rel.endswith('.LICENSE.txt'), 'Unexpected public file')
        require({'ui/index.html', 'ui/iframe.html', 'ui/index.json', 'ui/deployment.json'} <= files.keys(), 'Missing UI files')
        if target == 'service':
            runtime = frozenset(name[4:] for name in files if name.startswith('api/'))
            require(runtime in RUNTIME_LAYOUTS, 'Incomplete API')
        entries = json.loads(files['ui/index.json'])['entries']
        if target == 'service':
            require(set(entries) == {'hr-vision-product--start', 'hr-vision-product--manager', 'hr-vision-product--candidate'}, 'Service contains mockup stories')
        else:
            require('hr-vision-employer-menu--gallery' in entries and not any(key.startswith('hr-vision-product--') for key in entries), 'Mockup/service separation failed')
        public = json.loads(files['ui/deployment.json'])
        require(all(public.get(key) == manifest[key] for key in ('target', 'branch', 'sha', 'run')), 'Public revision differs')
        for name, data in files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)
        (destination / 'release.json').write_text(json.dumps(manifest))
        return manifest


def current_branch_sha(branch):
    request = urllib.request.Request(f'https://api.github.com/repos/{REPO}/git/ref/heads/{branch}', headers={'User-Agent': 'hr-vision-deployer', 'Accept': 'application/vnd.github+json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)['object']['sha']
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)


def pointer(base):
    p = base / 'current'
    require(not p.exists() or p.is_symlink(), 'Current must be a symlink')
    value = os.readlink(p) if p.is_symlink() else None
    require(value is None or re.fullmatch(r'releases/[A-Za-z0-9._-]+', value), 'Unsafe existing pointer')
    return value


def switch(base, name, value):
    temporary = base / ('.' + name + '-' + str(os.getpid()))
    if temporary.is_symlink():
        temporary.unlink()
    if value is None:
        (base / name).unlink(missing_ok=True)
    else:
        temporary.symlink_to(value)
        temporary.replace(base / name)


def api_changed(source):
    incoming = frozenset(path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file())
    require(incoming in RUNTIME_LAYOUTS, 'Incomplete API')
    installed = {name for name in RUNTIME if (API / 'current' / name).is_file()}
    # Adding or removing a complete runtime/package is an API change even if
    # shared files are identical; rollback/backup/restart cover both directions.
    return installed != incoming or any(
        (source / name).read_bytes() != (API / 'current' / name).read_bytes() for name in incoming)


def guard_live_interviews(db):
    states = db.execute('SELECT body FROM scenario WHERE id=1').fetchall()
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scenario_variants'").fetchone():
        states += db.execute('SELECT body FROM scenario_variants').fetchall()
    for row in states:
        state = json.loads(row[0])
        require(not any(c.get('meeting') and c['meeting']['status'] == 'live' for c in state['candidates'].values()), 'An interview is live. Retry after it finishes; current release is unchanged.')
    for table in ('cp_interviews', 'cp_meetings', 'ml_sessions'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            require(not db.execute(f"SELECT 1 FROM {table} WHERE status='live' LIMIT 1").fetchone(),
                    'An interview is live. Retry after it finishes; current release is unchanged.')


def guard_and_backup(release):
    with sqlite3.connect(f'file:{DATABASE}?mode=ro', uri=True) as db:
        guard_live_interviews(db)
        folder = Path('/var/backups/hr-vision')
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = folder / (release + '.sqlite3')
        with sqlite3.connect(backup) as copy:
            db.backup(copy)
        backup.chmod(0o600)


def restart_api():
    subprocess.run(['systemctl', 'restart', 'hr-vision-api'], check=True, timeout=45)


def stop_api():
    # The HTTP producers and shared dispatcher run in this unit's control group.
    # Wait for them to exit before inspecting data; the deploy flock alone does
    # not exclude a meeting transition or a worker commit.
    subprocess.run(['systemctl', 'stop', 'hr-vision-api'], check=True, timeout=45)


MATERIALS_UNITS = ('hr-vision-core-outbox', 'hr-vision-data-services',
                  'hr-vision-materials-scheduler', 'hr-vision-materials-worker')


def pause_materials():
    """Drain separate SQLite writers before backup and API pointer changes."""
    active = []
    try:
        for unit in MATERIALS_UNITS:
            state = subprocess.run(['systemctl', 'show', unit, '--property=LoadState', '--value'],
                                   check=True, capture_output=True, text=True, timeout=10).stdout.strip()
            if state == 'not-found':
                continue
            require(state == 'loaded', 'Cannot verify materials unit: ' + unit)
            running = subprocess.run(['systemctl', 'is-active', unit], capture_output=True,
                                     text=True, timeout=10).stdout.strip()
            require(running in ('active', 'inactive', 'failed'), 'Cannot verify materials state: ' + unit)
            if running != 'active':
                continue
            active.append(unit)
            subprocess.run(['systemctl', 'stop', unit], check=True, timeout=345)
            stopped = subprocess.run(['systemctl', 'is-active', unit], capture_output=True,
                                     text=True, timeout=10).stdout.strip()
            require(stopped == 'inactive', 'Materials unit did not drain: ' + unit)
    except Exception:
        resume_materials(active)
        raise
    return active


def resume_materials(active):
    for unit in reversed(active):
        subprocess.run(['systemctl', 'start', unit], check=True, timeout=45)


def guard_outbox_runtime(db, source):
    runtime = frozenset(path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file())
    require(runtime in RUNTIME_LAYOUTS, 'Incomplete recovery API')
    if 'assessment_jobs.py' not in runtime:
        for table in ('as_bindings', 'as_matches'):
            schema = db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchall()
            if schema:
                require(schema == [('table',)], 'Cannot verify Jev queue schema')
                require(not db.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone(),
                        'Jev queue data requires assessment_jobs.py; use a compatible forward recovery')
    if 'selection.py' not in runtime:
        for table in ('sl_core_results', 'sl_nominations'):
            schema = db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchall()
            if schema:
                require(schema == [('table',)], 'Cannot verify selection schema')
                require(not db.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone(),
                        'Selection data requires selection.py; use a compatible forward recovery')
    if 'interview_assessment.py' not in runtime:
        for table in ('ia_assessment_revisions', 'ia_assessment_feedback',
                      'ia_assessment_feedback_events', 'ia_assessment_commands'):
            schema = db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchall()
            if schema:
                require(schema == [('table',)], 'Cannot verify interview assessment schema: ' + table)
                # Keep immutable projections, fragment feedback, audit history
                # and retry identities readable. Never clear them to permit a
                # downgrade; an empty historical schema remains reversible.
                require(not db.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone(),
                        'Interview assessment data requires interview_assessment.py; use a compatible forward recovery')
    if not SERVICES_RUNTIME.issubset(runtime):
        for table in ('portal_materials_outbox', 'core_material_receipts', 'core_material_channels', 'core_material_asset_bindings'):
            schema = db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchall()
            if schema:
                require(schema == [('table',)], 'Cannot verify core/source schema: ' + table)
                # Even completed delivery intents and receipts preserve replay
                # identities. Old collectors must not resume ownership of them.
                require(not db.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone(),
                        'Core/source data requires services runtime; use a compatible forward recovery')
    if not ZOOM_RUNTIME.issubset(runtime):
        schema = db.execute("SELECT type FROM sqlite_master WHERE name='cp_call_provider_choices'").fetchall()
        if schema:
            require(schema == [('table',)], 'Cannot verify call provider choices schema')
            columns = {row[1] for row in db.execute('PRAGMA table_info(cp_call_provider_choices)')}
            require('provider' in columns, 'Cannot verify call provider choices schema')
            # Even a selected/not-yet-created Zoom meeting must never silently
            # reopen as Daily under an older runtime. Keep the durable choice.
            require(not db.execute("SELECT 1 FROM cp_call_provider_choices WHERE provider='zoom' LIMIT 1").fetchone(),
                    'Zoom meetings require Zoom runtime; use a compatible forward recovery')
    if not MATERIALS_RUNTIME.issubset(runtime):
        schema = db.execute("SELECT type FROM sqlite_master WHERE name='media_sources'").fetchall()
        if schema:
            require(schema == [('table',)], 'Cannot verify materials catalog schema')
            # Terminal/disabled sources still reference immutable transcripts and
            # owned objects. Old code cannot safely interpret or preserve them.
            require(not db.execute('SELECT 1 FROM media_sources LIMIT 1').fetchone(),
                    'Materials catalog requires materials runtime; use a compatible forward recovery')
    if 'candidate_availability.py' not in runtime:
        schema = db.execute("SELECT type FROM sqlite_master WHERE name='cp_candidates'").fetchall()
        if schema:
            require(schema == [('table',)], 'Cannot verify candidate calendar schema')
            for raw, in db.execute('SELECT profile FROM cp_candidates'):
                profile = json.loads(raw)
                require(isinstance(profile, dict), 'Cannot verify candidate calendar profile')
                schedule = profile.get('availabilitySchedule') or {}
                require(not schedule.get('weekly') and not schedule.get('dates') and not profile.get('timezone'),
                        'Candidate calendar preferences require candidate_availability.py; use a compatible forward recovery')
    if 'call_cleanup.py' not in runtime:
        schema = db.execute("SELECT type FROM sqlite_master WHERE name='cp_call_cleanup'").fetchall()
        # Even an empty queue is not enough: the durable cancellation trigger
        # would keep creating jobs under old code with no worker to consume them.
        require(not schema, 'Call cleanup schema requires call_cleanup.py; use a compatible forward recovery')
    if 'meeting_notifications.py' in runtime:
        return
    schema = db.execute("SELECT type FROM sqlite_master WHERE name='cn_outbox'").fetchall()
    if not schema:
        return  # A historical database before the shared outbox existed.
    require(schema == [('table',)], 'Cannot verify shared outbox schema')
    columns = {row[1] for row in db.execute('PRAGMA table_info(cn_outbox)')}
    require('event_type' in columns, 'Cannot verify shared outbox event types')
    payload = 'payload_json' if 'payload_json' in columns else 'NULL'
    for event, raw in db.execute(f'SELECT event_type,{payload} FROM cn_outbox'):
        require(event != 'meeting.completed', 'Lifecycle outbox requires meeting_notifications.py; use a compatible forward recovery')
        saved = json.loads(raw) if raw is not None else {}
        require(isinstance(saved, dict), 'Cannot verify shared outbox payload')
        # Include terminal rows: older retry routes may revive them. Never
        # delete, quarantine or rewrite recipient snapshots to permit rollback.
        require(not saved.get('lifecycle'), 'Lifecycle outbox requires meeting_notifications.py; use a compatible forward recovery')


def activate_api(value):
    """Only start code after checking the stopped writer's durable state."""
    stop_api()
    require(value is not None, 'No previous API; use a compatible forward recovery')
    # mode=rw must not silently create an empty DB on a bad/missing path. The
    # write reservation waits for an in-flight commit and spans check + switch.
    # Release it before starting the unit so initialization can write normally.
    with closing(sqlite3.connect(f'file:{DATABASE}?mode=rw', uri=True)) as db:
        with db:
            db.execute('BEGIN IMMEDIATE')
            guard_outbox_runtime(db, API / value)
            switch(API, 'current', value)
    restart_api()


def check_origin(target, sha):
    host = 'hr-vision.158-160-179-53.sslip.io' if target == 'service' else 'hr-vision-lab.158-160-179-53.sslip.io'
    deadline = time.monotonic() + 90
    first = last = None

    def request_timeout():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Origin readiness deadline expired')
        return min(4, remaining)

    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request('http://127.0.0.1:8381/deployment.json', headers={'Host': host})
            with urllib.request.urlopen(request, timeout=request_timeout()) as response:
                data = json.load(response)
            require(data['sha'] == sha and data['target'] == target, 'Origin revision mismatch')
            story = 'hr-vision-product--start' if target == 'service' else 'hr-vision-employer-menu--gallery'
            entry = 'http://127.0.0.1:8381/iframe.html?id=' + story + '&viewMode=story'
            with urllib.request.urlopen(urllib.request.Request(entry, headers={'Host': host}), timeout=request_timeout()) as response:
                require(response.status == 200 and response.url == entry, 'Entry page was redirected to another site')
            if target == 'service':
                with urllib.request.urlopen('http://127.0.0.1:8391/api/hr/health', timeout=request_timeout()) as response:
                    require(json.load(response).get('ok'), 'API health check failed')
            return
        except Exception as error:
            if first is None:
                first = error
            last = error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1, remaining))
    if first is None:
        first = last = TimeoutError('Origin readiness deadline expired before the first check')
    raise RuntimeError('Origin verification failed; first error: ' + type(first).__name__ + ': ' + str(first)
                       + '; last error: ' + type(last).__name__ + ': ' + str(last)) from first


def retain_replay_assets(previous, destination):
    """Keep missing public replay assets for 25h without duplicating their bytes."""
    if previous is None or not previous.is_dir():
        return
    now = time.time()
    ledger = previous / '.replay-assets.json'
    expiries = json.loads(ledger.read_text()) if ledger.exists() else {}
    retained = {}
    for source in (previous / 'assets').rglob('*'):
        name = source.relative_to(previous).as_posix()
        if source.is_symlink() or not source.is_file() or source.suffix.lower() not in {
            '.css', '.woff', '.woff2', '.ttf', '.otf', '.svg', '.png', '.webp', '.ico',
        }:
            continue
        output = destination / name
        expires = expiries.get(name, now + 25 * 3600)
        if output.exists() or expires <= now:
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, output)
        retained[name] = expires
    (destination / '.replay-assets.json').write_text(json.dumps(retained))


def activate(stage, manifest):
    target, release = manifest['target'], manifest['release']
    base = BASES[target]
    base.mkdir(parents=True, exist_ok=True)
    require(shutil.disk_usage(base).free >= 512 * 1024 * 1024 + MAX_CONTENT, 'Insufficient disk reserve')
    # Do not let an older workflow overwrite a more recent push.
    require(current_branch_sha(manifest['branch']) == manifest['sha'], 'Branch advanced; this release is superseded')
    previous_ui = pointer(base)
    previous_api = pointer(API) if target == 'service' else None
    change_api = target == 'service' and api_changed(stage / 'api')
    paused = pause_materials() if change_api else []
    safe_to_resume = True
    try:
        if change_api:
            guard_and_backup(release)
        ui_release = base / 'releases' / release
        require(not ui_release.exists(), 'Workflow attempt was already installed; rerun it for a new attempt')
        ui_release.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(stage / 'ui', ui_release)
        if target == 'service':
            retain_replay_assets(base / previous_ui if previous_ui else None, ui_release)
        if change_api:
            api_release = API / 'releases' / release
            require(not api_release.exists(), 'API release already exists')
            shutil.copytree(stage / 'api', api_release)
        attempted_api = False
        try:
            if change_api:
                attempted_api = True
                activate_api('releases/' + release)
            switch(base, 'current', 'releases/' + release)
            check_origin(target, manifest['sha'])
        except Exception as failure:
            if attempted_api:
                try:
                    # The new runtime may have committed lifecycle rows during its
                    # health check. Recheck after stopping it, never reuse preflight.
                    activate_api(previous_api)
                except Exception as recovery:
                    safe_to_resume = False
                    raise RuntimeError('API rollback refused or failed; receiver will not restart an unsafe runtime. '
                                       'Verify unit state and deploy a compatible forward recovery. '
                                       'Original failure: ' + type(failure).__name__ + ': ' + str(failure)
                                       + '; rollback failure: ' + type(recovery).__name__ + ': ' + str(recovery)) from failure
            switch(base, 'current', previous_ui)
            raise
        if previous_ui:
            switch(base, 'previous', previous_ui)
        if attempted_api and previous_api:
            switch(API, 'previous', previous_api)
        print(json.dumps({'published': target, 'sha': manifest['sha'], 'release': release, 'apiRestarted': change_api}), flush=True)
    finally:
        if safe_to_resume:
            resume_materials(paused)


def main():
    require(os.geteuid() == 0 and len(sys.argv) == 2 and sys.argv[1] in BRANCHES, 'Invalid deploy command')
    target = sys.argv[1]
    os.umask(0o022)
    with tempfile.TemporaryDirectory(prefix='hr-vision-ci-') as temporary:
        temp = Path(temporary)
        archive = temp / 'release.tgz'
        total = 0
        with archive.open('wb') as output:
            while data := sys.stdin.buffer.read(65536):
                total += len(data)
                require(total <= MAX_ARCHIVE, 'Upload too large')
                output.write(data)
        stage = temp / 'stage'
        stage.mkdir()
        manifest = unpack(archive, stage, target)
        base = BASES[target]
        base.mkdir(parents=True, exist_ok=True)
        with (base / '.deploy.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if target == 'service':
                with Path('/run/hr-vision-api-deploy.lock').open('a') as api_lock:
                    fcntl.flock(api_lock, fcntl.LOCK_EX)
                    activate(stage, manifest)
            else:
                activate(stage, manifest)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Deployment failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
