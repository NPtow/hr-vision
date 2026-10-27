"""Run as root on dsa. Adds only this site's import, with validation and compare-before-replace."""
from pathlib import Path
import hashlib
import os
import subprocess
import time

site = Path('/etc/caddy/pulse-intake.caddy')
content = '''pulse.158-160-179-53.sslip.io {
    encode zstd gzip
    request_body {
        max_size 5MB
    }
    reverse_proxy 10.130.0.18:8393
}
'''
if site.exists() and site.read_text() != content:
    raise RuntimeError('Existing site differs; inspect before updating')
site.write_text(content)
site.chmod(0o644)
main = Path('/etc/caddy/Caddyfile')
original = main.read_bytes()
line = b'import /etc/caddy/pulse-intake.caddy'
if line not in original:
    if b'pulse.158-160-179-53.sslip.io' in original:
        raise RuntimeError('Hostname already configured elsewhere')
    candidate = main.with_name('Caddyfile.pulse-candidate')
    candidate.write_bytes(original.rstrip()+b'\n\n'+line+b'\n')
    candidate.chmod(0o644)
    result = subprocess.run(['caddy','validate','--config',str(candidate),'--adapter','caddyfile'],capture_output=True)
    if result.returncode:
        raise RuntimeError('Caddy candidate validation failed; original untouched')
    if main.read_bytes() != original:
        raise RuntimeError('Caddy changed concurrently; original untouched')
    backup = main.with_name('Caddyfile.before-pulse-'+time.strftime('%Y%m%dT%H%M%S'))
    backup.write_bytes(original); backup.chmod(0o600)
    os.replace(candidate, main)
    result = subprocess.run(['systemctl','reload','caddy'],capture_output=True)
    if result.returncode:
        if main.read_bytes().endswith(line+b'\n'):
            main.write_bytes(original)
        raise RuntimeError('Caddy reload failed; configuration restored')
    print('Caddy validated and reloaded. Backup:',backup.name)
else:
    print('Site import already present; no shared reload needed.')
print('Public route installed.')
