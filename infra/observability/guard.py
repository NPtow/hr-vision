#!/usr/bin/env python3
"""Stop only the telemetry collector before it competes for the last disk space.

ClickHouse's own 1.5 GiB reserve is the second line of defense. Its TTL merges
continue while ingestion is stopped, so the guard can resume after space returns.
"""
import json
from pathlib import Path
import shutil
import subprocess
import time

base = Path('/var/lib/mashadasha-observability')
free = shutil.disk_usage(base).free
name = 'mashadasha-observability-collector-1'
state = subprocess.run(['docker','inspect','--format','{{.State.Running}}',name],capture_output=True,text=True,check=True).stdout.strip()
marker = base/'disk-paused'
if free < 1700 * 1024**2 and state == 'true':
    subprocess.run(['docker','stop','--time','10',name],check=True,capture_output=True)
    marker.touch()
elif free > 1900 * 1024**2 and marker.exists() and state == 'false':
    subprocess.run(['docker','start',name],check=True,capture_output=True)
    marker.unlink()
(base/'guard-status.json').write_text(json.dumps({'checkedAt':int(time.time()),'freeBytes':free,'pausedByDiskGuard':marker.exists()})+'\n')
if marker.exists():
    print('ClickStack ingestion paused: disk reserve reached. Product services are unchanged.')
