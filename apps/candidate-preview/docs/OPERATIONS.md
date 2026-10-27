# Candidate preview operations

Prepared 2026-10-26; operator: HR Vision maintainer. Deployment evidence is recorded below and in ACCEPTANCE.md. Stateless Python stdlib BFF and prebuilt web/dist run on outreach independently of the production API. No database, background job or writable runtime directory is required. Caller authorization is validated by the existing manager API before each protected materials adapter operation. The server-only adapter reads canonical Core/Source projections using existing service credentials; it exposes no business write commands. Credentials never enter frontend assets, responses or logs. Do not use ~/deploy.sh or restart hr-vision-api.

## Verified baseline (read-only SSH, 2026-10-26)

- Outreach 10.130.0.18 listens on 127.0.0.1:8381 and 10.130.0.18:8381. Port 8397 was free.
- /opt/hr-vision-api/current resolves to /opt/hr-vision-api/releases/6a5b7e3f70dcea06605180d5d031aa160dd799f8-40371683616.1.
- /srv/hr-vision/current resolves to /srv/hr-vision/releases/6a5b7e3f70dcea06605180d5d031aa160dd799f8-40371683616.1.
- dsa /etc/caddy/Caddyfile has hr-vision.158-160-179-53.sslip.io and mashadasha.tech site blocks, each reverse_proxy 10.130.0.18:8381 with header_up X-HR-Client-IP {remote_host}.

SSH outreach through dsa (use project known_hosts if available; never disable host verification):

```sh
ssh -o 'ProxyCommand=ssh -i ~/.ssh/yandex_ebitrix -W %h:%p nikita@158.160.179.53' -i ~/.ssh/yandex_ebitrix nikita@10.130.0.18
```

## Build and package locally

For this initial release, frontend dependencies come from the repository's `apps/web/node_modules` via an ignored symlink. Run `npm run build` in web and the service tests. Production receives built assets, not node_modules, and requires only system Python. Inspect final web/dist and ensure its asset URLs start /candidate-preview/. From service directory:

```sh
python3 deploy/package-release.py --release SOURCE_SHA-BUILD_ID --output /tmp/candidate-preview-SOURCE_SHA-BUILD_ID.tar.gz
```

The packager fails if dist/index.html is absent, refuses to overwrite an archive, includes an explicit server safelist (app.py, adapter.py, adapter_core.py, adapter_reader.py, adapter_errors.py and VENDORED.md provenance), unit and static assets, and emits an archive SHA256. manifest.json records release identity and every payload hash. It does not collect repository environment files, node_modules or Python caches. Record commit, build command, test results and archive hash alongside acceptance evidence. Transfer this archive only to outreach using the same SSH hop; independently compare SHA256 after transfer.

## Install and atomic update (operator only)

The unit binds only 127.0.0.1:8397. No public 8397 exposure is needed. Confirm upstream is 127.0.0.1:8391, the API listener behind the existing origin Caddy. Unit values deliberately override development defaults.

The unit requires `/etc/candidate-profile-preview/services.env`, root-owned mode 600 in a root-owned mode 700 directory. The parent operator exports only `HR_MATERIALS_CORE_URL`, `HR_MATERIALS_CORE_KEY`, `HR_MATERIALS_SOURCE_URL`, and `HR_MATERIALS_SOURCE_KEY` from the existing API environment using a local parser without printing values. Core URL/key are required; Source URL/key are included only when configured. Do not source arbitrary environment text, copy the entire api.env, or put secrets in an archive, repository, frontend build, shell arguments or terminal output. Systemd reads EnvironmentFile as root before dropping privileges to candidate-preview. Validate file ownership/permissions and key names without showing values. Preserve this file across releases and rollback; do not change canonical service credentials.

On outreach set shell variables `release=SOURCE_SHA-BUILD_ID` and `archive=/tmp/candidate-preview-SOURCE_SHA-BUILD_ID.tar.gz` to the inspected artifact. Validate release is only letters, digits, dot, dash and underscore. Run sequentially; stop on any error:

```sh
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin candidate-preview
sudo install -d -m 755 /opt/candidate-profile-preview/releases
sudo mkdir /opt/candidate-profile-preview/releases/"$release".staging
sudo tar -xzf "$archive" --no-same-owner -C /opt/candidate-profile-preview/releases/"$release".staging
```

`useradd` is first-install only: if account exists, verify it rather than recreate. Verify all manifest paths remain under staging, all hashes match, and no unexpected files/symlinks are present before promoting. Make payload root-owned and readable by service user. Do not extract untrusted archives.

```sh
sudo chown -R root:root /opt/candidate-profile-preview/releases/"$release".staging
sudo chmod -R u=rwX,go=rX /opt/candidate-profile-preview/releases/"$release".staging
sudo mv /opt/candidate-profile-preview/releases/"$release".staging /opt/candidate-profile-preview/releases/"$release"
readlink -f /opt/candidate-profile-preview/current
sudo install -m 644 /opt/candidate-profile-preview/releases/"$release"/deploy/candidate-profile-preview.service /etc/systemd/system/candidate-profile-preview.service
sudo systemd-analyze verify /etc/systemd/system/candidate-profile-preview.service
sudo ln -s /opt/candidate-profile-preview/releases/"$release" /opt/candidate-profile-preview/current.next
sudo mv -Tf /opt/candidate-profile-preview/current.next /opt/candidate-profile-preview/current
sudo systemctl daemon-reload
sudo systemctl enable --now candidate-profile-preview
sudo systemctl restart candidate-profile-preview
curl --fail --max-time 15 http://127.0.0.1:8397/candidate-preview/api/health
```

Record previous current target and previous unit before update. First install may have no current link. Do not overwrite an existing release directory or leave current.next from an interrupted update unchecked. Only preview service restarts.

## Route existing origin on outreach

Live inspection confirms :8381 is dedicated hr-vision-web Caddy, whose ExecStart reads /etc/hr-vision/Caddyfile and whose admin API binds 127.0.0.1:2139. Its `handle @trusted` allows dsa/localhost and the HR Vision/mashadasha Host headers. Existing /api/hr/* routes to 127.0.0.1:8391. Therefore retain dsa unchanged and add preview within this trusted block.

Back up /etc/hr-vision/Caddyfile with a unique timestamp and record its path. Insert deploy/caddy-path.caddy.example inside existing `handle @trusted`, before its final fallback handle. Keep all existing routes, trusted-host checks and headers. Preserve the /candidate-preview prefix and leave /api/hr/session owned by the original API.

```sh
sudo caddy validate --config /etc/hr-vision/Caddyfile --adapter caddyfile
sudo caddy reload --config /etc/hr-vision/Caddyfile --adapter caddyfile --address 127.0.0.1:2139
```

This reload targets only dedicated HR Vision web Caddy; do not restart shared outreach Caddy or API. Before routing check local preview health. After routing verify through HTTPS /candidate-preview/ HTML, assets, health and unauthorized API behavior. In an authenticated manager browser using its existing invitation/session, verify complete candidate pagination, profile, saved assessment and interview/material playback. Do not paste tokens into shell/history/logs. Verify original cabinet and /api/hr/session still work. A health response alone does not establish acceptance.

## Diagnosis, observation and rollback

```sh
sudo systemctl status candidate-profile-preview --no-pager
sudo systemctl show candidate-profile-preview -p MemoryCurrent -p MemoryMax -p NRestarts
sudo journalctl -u candidate-profile-preview --since '-10 minutes' --no-pager
ss -ltn
```

Logs must not include authorization, source URLs, transcripts or candidate payloads. Observe normal browser operations and bounded memory/errors; attach timestamps and release manifest to the acceptance report. Restart recovery, dependency outage, timeout/concurrency behavior and rollback must be exercised in isolation before claiming production readiness.

For update rollback, set `previous` to the recorded absolute release path, verify manifest, atomically create current.next pointing there and `mv -Tf` onto current, restore that release's recorded unit if changed, daemon-reload and restart only candidate-profile-preview. Recheck health and authenticated browser scenario. No data rollback is needed because the preview stores no business state.

For first-install removal or routing failure, restore the recorded /etc/hr-vision/Caddyfile backup, validate and reload dedicated Caddy at 127.0.0.1:2139, verify existing cabinet, then stop/disable candidate-profile-preview on outreach. Retain release/archive/evidence until acceptance. Do not remove or change production API/static symlinks.

## Deployment receipt — 2026-10-26

- URL: https://hr-vision.158-160-179-53.sslip.io/candidate-preview/
- Current: `/opt/candidate-profile-preview/releases/preview-20261026-03`.
- Archive: `candidate-preview-20261026-03.tar.gz`; SHA256 `041106457f8bc5aed3be38781428ce5d708eac570663c7841cbd6abe2f08c4df`. All 10 payload hashes verified before activation. Source is a new uncommitted service directory; artifact identity identifies this release.
- Caddy backup: `/etc/hr-vision/Caddyfile.before-candidate-preview-20261026`. Only dedicated HR web Caddy was reloaded through admin `127.0.0.1:2139`; dsa routing was not changed.
- `services.env` contains only the four named variables from existing `/etc/hr-vision/api.env`, `/etc/hr-vision-services/runtime.env`, and `/etc/hr-vision/materials.env`. Verified `600 root root`; values were not printed.
- Exercised 03 → rollback 02 → restore 03, health after each preview-only restart. Final service active/running, NRestarts=0, MemoryCurrent=25,079,808 bytes, MemoryMax=268,435,456 bytes. This is a short observation, not endurance acceptance.
- Original API/static symlinks were not modified by this task. Another release appeared during work: final observed original API/static release `de775962b67feebc3199ac12f59b283675a26b3c-40377993500.1` supersedes the baseline. Aygerim was verified again against this current API.
- Temporary API-smoke bearer and full-assessment files were removed after checks. Browser retains its ordinary manager session; canonical service credentials remain only in the protected service environment.
- See ACCEPTANCE.md for browser and test evidence. Mobile/device and sustained-load acceptance remain open.

## Update 04 — reference-faithful interface and recorded-interview pool

Current release `preview-20261026-04`; archive SHA256 `3674fef651e35ab6b2b2162c97017bad7cc73ccea52bf26f28f4fc0dd43062c7`, 13 files verified. Atomic activation and preview-only restart succeeded; previous03 preserved for rollback. No route, environment or primary application changes. Candidate directory now performs a bounded Core scan (4 concurrent,25-second deadline, maximum2000 input candidates, single directory scan in flight). See acceptance for actual7 and independent31+19 tests.

Current visual correction05: archive SHA256 `5eeb2ecf809728e7e4f5c3ae52ab3a9a379374d8893bc20ee01cb6f4af61df67`,13files verified; preview-only atomic activation+health passed.04 kept for rollback. Fixed conditional CSS media wrappers and repeated interview title after real1440px screenshot.
