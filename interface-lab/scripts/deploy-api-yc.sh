#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release="$(date -u +%Y%m%dT%H%M%SZ)"
ssh_args=(-o BatchMode=yes -o ConnectTimeout=15 -o "ProxyCommand=ssh -o BatchMode=yes -o ConnectTimeout=15 -o ProxyCommand=none -o ProxyJump=none -i $HOME/.ssh/yandex_ebitrix -W %h:%p nikita@158.160.179.53" -i "$HOME/.ssh/yandex_ebitrix")
remote='nikita@10.130.0.18'
staging="/tmp/hr-vision-api-$release"
ssh "${ssh_args[@]}" "$remote" mkdir -m 700 "$staging"
scp "${ssh_args[@]}" "$project_dir"/server/*.py "$project_dir/deploy/hr-vision-api.service" "$project_dir/deploy/outreach.Caddyfile" "$remote:$staging/"
ssh "${ssh_args[@]}" "$remote" sudo bash -s -- "$staging" "$release" <<'REMOTE'
set -euo pipefail
staging="$1"
release="$2"
base=/opt/hr-vision-api
exec 9>/run/hr-vision-api-deploy.lock
flock -x 9
available=$(df --output=avail -k /var/lib | tail -1 | tr -d ' ')
test "$available" -gt 524288
cd "$staging"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_domain test_daily_media test_feedback test_browser_access test_dsa_panel test_feedback_http
caddy validate --adapter caddyfile --config "$staging/outreach.Caddyfile"
# Compare active config to the owned file before a graceful reload.
caddy adapt --adapter caddyfile --config /etc/hr-vision/Caddyfile 2>/dev/null > "$staging/file.json"
curl -fsS http://127.0.0.1:2139/config/ > "$staging/active.json"
python3 - "$staging" <<'PY'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1])
a=json.loads((p/'file.json').read_text());b=json.loads((p/'active.json').read_text())
assert a == b, 'Active HR Vision Caddy differs from file; stop before replacing.'
PY
if ! id hr-vision >/dev/null 2>&1; then useradd --system --home-dir /var/lib/hr-vision-api --no-create-home --shell /usr/sbin/nologin hr-vision; fi
install -d -m 755 "$base/releases/$release"
install -m 644 "$staging"/*.py "$base/releases/$release/"
install -d -m 700 -o hr-vision -g hr-vision /var/lib/hr-vision-api
if [ ! -f /etc/hr-vision/api.env ]; then
  python3 - <<'PY'
import os, pathlib, secrets
p=pathlib.Path('/etc/hr-vision/api.env')
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
 f.write('HR_TEAM_KEY='+secrets.token_urlsafe(24)+'\nHR_PORT=8391\nHR_VIDEO_ENABLED=0\nDAILY_API_KEY=\n')
PY
fi
previous=$(readlink "$base/current" 2>/dev/null || true)
cp /etc/hr-vision/Caddyfile "/etc/hr-vision/Caddyfile.before-api-$release"
ln -s "releases/$release" "$base/current-$release"
mv -Tf "$base/current-$release" "$base/current"
install -m 644 "$staging/hr-vision-api.service" /etc/systemd/system/hr-vision-api.service
systemd-analyze verify /etc/systemd/system/hr-vision-api.service
systemctl daemon-reload
systemctl enable --now hr-vision-api
systemctl restart hr-vision-api
if ! curl -fsS --retry 5 --retry-delay 1 --retry-all-errors http://127.0.0.1:8391/api/hr/health; then
  if [ -n "$previous" ]; then ln -s "$previous" "$base/rollback-$release"; mv -Tf "$base/rollback-$release" "$base/current"; systemctl restart hr-vision-api; fi
  exit 1
fi
install -o root -g caddy -m 640 "$staging/outreach.Caddyfile" /etc/hr-vision/Caddyfile
if ! systemctl reload hr-vision-web; then
  cp "/etc/hr-vision/Caddyfile.before-api-$release" /etc/hr-vision/Caddyfile
  systemctl reload hr-vision-web
  exit 1
fi
curl -fsS -H 'Host: hr-vision.158-160-179-53.sslip.io' http://127.0.0.1:8381/api/hr/health
rm -rf "$staging"
printf '\nAPI release %s active on outreach.\n' "$release"
REMOTE
curl -fsS https://hr-vision.158-160-179-53.sslip.io/api/hr/health
