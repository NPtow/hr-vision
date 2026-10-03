#!/usr/bin/env bash
# Install deploy/Caddyfile once before running this script. No server build,
# application daemon, shared-lab change, or automatic release cleanup is needed.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
site_url='https://hr-vision.158-160-179-53.sslip.io'
remote_host='nikita@158.160.179.53'
ssh_args=(-o BatchMode=yes -o ConnectTimeout=15 -o ProxyCommand=none -o ProxyJump=none -i "$HOME/.ssh/yandex_ebitrix")
release="$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
temp_dir="$(mktemp -d -t hr-vision-deploy)"
archive="$temp_dir/site.tgz"
build_dir="$temp_dir/site"
publish_attempted=0
upload_attempted=0
expected_current=__none__
expected_previous=__none__

cleanup() {
  status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$publish_attempted" -eq 1 ]; then
    echo 'Publish or verification failed; attempting a conditional rollback.' >&2
    ssh "${ssh_args[@]}" "$remote_host" sudo bash -s -- "$release" "$expected_current" "$expected_previous" <<'ROLLBACK' || echo 'Automatic rollback could not be confirmed; inspect /srv/hr-vision/current.' >&2
set -euo pipefail
base=/srv/hr-vision
release="$1"
expected_current="$2"
expected_previous="$3"
exec 9>"$base/.deploy.lock"
flock -x 9
if [ "$(readlink "$base/current" 2>/dev/null || true)" != "releases/$release" ]; then
  echo 'Current was not switched to this release, or another deploy superseded it; left unchanged.'
  exit 0
fi
if [ "$expected_current" = __none__ ]; then
  rm "$base/current"
else
  ln -s "$expected_current" "$base/rollback-$release"
  mv -Tf "$base/rollback-$release" "$base/current"
fi
if [ "$expected_previous" = __none__ ]; then
  rm -f "$base/previous"
else
  ln -s "$expected_previous" "$base/previous-rollback-$release"
  mv -Tf "$base/previous-rollback-$release" "$base/previous"
fi
echo 'Previous pointers restored; failed release retained for inspection.'
ROLLBACK
  fi
  if [ "$status" -ne 0 ] && [ "$upload_attempted" -eq 1 ]; then
    ssh "${ssh_args[@]}" "$remote_host" rm -f "/tmp/hr-vision-$release.tgz" || echo "Could not clean up /tmp/hr-vision-$release.tgz; inspect this exact file." >&2
  fi
  rm -rf "$temp_dir"
  exit "$status"
}
trap cleanup EXIT

# Read both pointers before the local build. Publication compares them again
# under a server lock, so another deploy cannot be silently overwritten.
remote_state="$(ssh "${ssh_args[@]}" "$remote_host" bash -s <<'STATE'
set -euo pipefail
for pointer in /srv/hr-vision/current /srv/hr-vision/previous; do
  if [ -L "$pointer" ]; then
    readlink "$pointer"
  elif [ -e "$pointer" ]; then
    echo "Expected a symlink: $pointer" >&2
    exit 1
  else
    echo __none__
  fi
done
STATE
)"
expected_current="$(printf '%s\n' "$remote_state" | sed -n '1p')"
expected_previous="$(printf '%s\n' "$remote_state" | sed -n '2p')"
for pointer in "$expected_current" "$expected_previous"; do
  if [[ "$pointer" != __none__ && ! "$pointer" =~ ^releases/[A-Za-z0-9._-]+$ ]]; then
    echo "Unexpected release pointer; stop and inspect: $pointer" >&2
    exit 1
  fi
done

cd "$project_dir"
npm run typecheck
npm run build -- --output-dir "$build_dir"
node --input-type=module - "$build_dir" <<'VERIFY_BUILD'
import { readFileSync, readdirSync } from 'node:fs';
const buildDir = process.argv[2];
const entries = JSON.parse(readFileSync(`${buildDir}/index.json`, 'utf8')).entries;
for (const id of [
  'hr-vision-xpm--overview', 'hr-vision-agency--overview',
  'hr-vision-ats-example--overview', 'hr-vision-cjm--contacts',
  'hr-vision-cjm--agency', 'hr-vision-candidate-cjm--agency',
  'hr-vision-candidate-cjm--other',
  'hr-vision-product--manager', 'hr-vision-product--candidate',
]) if (!entries[id]) throw new Error(`Missing required story: ${id}`);
function inspect(dir, prefix = '', publicOnly = false) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = `${dir}/${entry.name}`;
    const relative = prefix + entry.name;
    if (entry.isSymbolicLink()) throw new Error(`Symlink in public build: ${path}`);
    if (/^(\.env(?:\..*)?|\.git|sources|granola|transcripts?)$/i.test(entry.name) || /\.(pdf|pem|key|jsonl)$/i.test(entry.name)) {
      throw new Error(`Private-source or credential-like file in public build: ${path}`);
    }
    if (entry.isDirectory()) inspect(path, `${relative}/`, publicOnly);
    else if (publicOnly) {
      if (!/^hr-cjm\/screens\/(?:a0[1-6]|e0[1-5]|h0[1-4]|c0[1-4])\.webp$/.test(relative)) {
        throw new Error(`Unapproved file in public/: ${relative}. Review it before extending the explicit allowlist.`);
      }
    } else if (!(/\.(?:js|css|svg|woff2?|ttf|png|jpe?g|webp|ico|gif)$/i.test(relative)
      || /^(?:index|iframe)\.html$/.test(relative)
      || /^(?:index|project)\.json$/.test(relative)
      || /(?:^|\/)\S+\.LICENSE\.txt$/.test(relative))) {
      throw new Error(`Unapproved file in generated build: ${relative}`);
    }
  }
}
inspect('public', '', true);
inspect(buildDir);
VERIFY_BUILD

# The archive includes only Storybook's public output, never the repository,
# research, meeting transcripts, .env files, or source documents.
node --input-type=module - "$build_dir" "$release" <<'MANIFEST'
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
const buildDir = process.argv[2];
writeFileSync(`${buildDir}/deployment.json`, JSON.stringify({ release: process.argv[3] }) + '\n');
const rows = [];
function visit(dir = '') {
  for (const item of readdirSync(`${buildDir}/${dir}`, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const name = dir + item.name;
    if (item.isDirectory()) visit(`${name}/`);
    else if (name !== 'SHA256SUMS') rows.push(`${createHash('sha256').update(readFileSync(`${buildDir}/${name}`)).digest('hex')}  ${name}`);
  }
}
visit();
writeFileSync(`${buildDir}/SHA256SUMS`, rows.join('\n') + '\n');
MANIFEST
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$archive" -C "$build_dir" .
archive_hash="$(shasum -a 256 "$archive" | awk '{print $1}')"
index_hash="$(shasum -a 256 "$build_dir/index.json" | awk '{print $1}')"
archive_kib="$(du -k "$archive" | awk '{print $1}')"
build_kib="$(du -sk "$build_dir" | awk '{print $1}')"
required_kib=$((524288 + archive_kib + build_kib))
ssh "${ssh_args[@]}" "$remote_host" bash -s -- "$required_kib" <<'DISK_CHECK'
set -euo pipefail
required_kib="$1"
for location in /srv /tmp; do
  available_kib="$(df --output=avail -k "$location" | tail -1 | tr -d ' ')"
  if [ "$available_kib" -lt "$required_kib" ]; then
    echo "Insufficient space on $location: ${available_kib} KiB free, ${required_kib} KiB required (including 512 MiB reserve)." >&2
    exit 1
  fi
done
DISK_CHECK
upload_attempted=1
scp "${ssh_args[@]}" "$archive" "$remote_host:/tmp/hr-vision-$release.tgz"
publish_attempted=1
ssh "${ssh_args[@]}" "$remote_host" sudo bash -s -- "$release" "$archive_hash" "$build_kib" "$expected_current" "$expected_previous" <<'PUBLISH'
set -euo pipefail
release="$1"
archive_hash="$2"
build_kib="$3"
expected_current="$4"
expected_previous="$5"
base=/srv/hr-vision
archive="/tmp/hr-vision-$release.tgz"
trap 'rm -f "$archive"' EXIT
printf '%s  %s\n' "$archive_hash" "$archive" | sha256sum --check --status
install -d -m 755 "$base" "$base/releases"
exec 9>"$base/.deploy.lock"
flock -x 9
read_pointer() {
  if [ -L "$1" ]; then readlink "$1";
  elif [ -e "$1" ]; then echo "Expected a symlink: $1" >&2; return 1;
  else echo __none__; fi
}
if [ "$(read_pointer "$base/current")" != "$expected_current" ] || [ "$(read_pointer "$base/previous")" != "$expected_previous" ]; then
  echo 'Release pointers changed while building. No publish performed; rerun from the current state.' >&2
  exit 1
fi
available_kib="$(df --output=avail -k "$base" | tail -1 | tr -d ' ')"
if [ "$available_kib" -lt "$((524288 + build_kib))" ]; then
  echo 'Not enough space for the release plus 512 MiB reserve.' >&2
  exit 1
fi
test ! -e "$base/releases/$release"
install -d -m 755 "$base/releases/$release"
tar --no-same-owner -xzf "$archive" -C "$base/releases/$release"
chown -R root:root "$base/releases/$release"
chmod -R u=rwX,go=rX "$base/releases/$release"
(cd "$base/releases/$release" && sha256sum --check --status SHA256SUMS)
for file in index.html iframe.html index.json deployment.json; do test -s "$base/releases/$release/$file"; done
available_kib="$(df --output=avail -k "$base" | tail -1 | tr -d ' ')"
test "$available_kib" -ge 524288
ln -s "releases/$release" "$base/current-$release"
mv -Tf "$base/current-$release" "$base/current"
if [ "$expected_current" != __none__ ]; then
  ln -s "$expected_current" "$base/previous-$release"
  mv -Tf "$base/previous-$release" "$base/previous"
fi
printf 'Activated release: %s\n' "$release"
PUBLISH

curl --fail --silent --show-error --retry 5 --retry-delay 2 --retry-all-errors --max-time 12 "$site_url/index.json" -o "$temp_dir/public-index.json"
test "$(shasum -a 256 "$temp_dir/public-index.json" | awk '{print $1}')" = "$index_hash"
curl --fail --silent --show-error --max-time 12 "$site_url/deployment.json" -o "$temp_dir/deployment.json"
node --input-type=module - "$temp_dir/deployment.json" "$release" <<'VERIFY_RELEASE'
import { readFileSync } from 'node:fs';
if (JSON.parse(readFileSync(process.argv[2], 'utf8')).release !== process.argv[3]) throw new Error('Public release does not match deployment.');
VERIFY_RELEASE
curl --fail --silent --show-error --max-time 12 "$site_url/iframe.html?id=hr-vision-xpm--overview&viewMode=story" -o /dev/null
landing="$(curl --fail --silent --show-error --max-time 12 --output /dev/null --write-out '%{redirect_url}' "$site_url/")"
test "$landing" = "$site_url/iframe.html?id=hr-vision-xpm--overview&viewMode=story"
printf '\nHR Vision is live: %s\nRelease: %s\nPrevious: %s\n' "$site_url" "$release" "$expected_current"
