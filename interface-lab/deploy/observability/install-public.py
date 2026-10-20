#!/usr/bin/env python3
"""Install only the HTTPS proxy on dsa; all stack containers stay on outreach."""
from pathlib import Path
import shutil
import subprocess
import time
source = Path('/tmp/mashadasha-observability.caddy')
fragment = Path('/etc/caddy/mashadasha-observability.caddy')
config = Path('/etc/caddy/Caddyfile')
previous = config.read_text()
backup = config.with_name('Caddyfile.pre-observability-'+str(int(time.time())))
shutil.copy2(config,backup)
shutil.copy2(source,fragment)
fragment.chmod(0o644)
text = previous
if 'import /etc/caddy/mashadasha-observability.caddy' not in text:
    assert 'observe.158-160-179-53.sslip.io' not in text, 'Panel host already configured'
    text += '\nimport /etc/caddy/mashadasha-observability.caddy\n'
config.write_text(text)
try:
    subprocess.run(['caddy','validate','--config',str(config),'--adapter','caddyfile'],check=True)
    subprocess.run(['systemctl','reload','caddy'],check=True)
except Exception:
    shutil.copy2(backup,config)
    raise
