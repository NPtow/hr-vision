#!/usr/bin/env python3
"""Install reversible origin routing and disk guard on outreach as root."""
from pathlib import Path
import shutil
import subprocess
import time
base = Path(__file__).resolve().parent
config = Path('/etc/hr-vision/Caddyfile')
previous = config.read_text()
text = previous
if 'origin-ingest.caddy' not in text:
    needle = '\thandle @trusted {\n'
    assert text.count(needle) == 1, 'Trusted host handler changed; inspect before installing'
    text = text.replace(needle, needle+'\t\timport /opt/mashadasha-observability/origin-ingest.caddy\n',1)
if 'origin-panel.caddy' not in text:
    text += '\nimport /opt/mashadasha-observability/origin-panel.caddy\n'
backup = config.with_name('Caddyfile.pre-observability-'+str(int(time.time())))
shutil.copy2(config,backup)
config.write_text(text)
try:
    subprocess.run(['caddy','validate','--config',str(config),'--adapter','caddyfile'],check=True)
    subprocess.run(['systemctl','reload','hr-vision-web'],check=True)
except Exception:
    shutil.copy2(backup,config)
    raise
for probe in ['guard','health']:
    for kind in ['service','timer']:
        shutil.copy2(base/('mashadasha-observability-'+probe+'.'+kind), '/etc/systemd/system/')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','mashadasha-observability-guard.timer','mashadasha-observability-health.timer'],check=True)
