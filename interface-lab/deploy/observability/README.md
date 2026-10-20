# MashaDasha observability

Stock HyperDX / ClickStack 2.19.0 at https://observe.158-160-179-53.sslip.io.
All four containers run on **outreach**. The dsa host only terminates HTTPS.
Images are pinned by digest. MongoDB and ClickHouse have no published ports;
UI/API/OTLP ports are bound to loopback. The origin panel accepts only the dsa
private address and localhost. Native HyperDX authentication is required;
registration is closed at the public proxy after initial owner creation.

## What is collected

- Production candidate, employer, recruiter and outreach browser screens: rrweb
  replay, browser errors, API timings, web vitals. Preview/lab/local hosts are
  excluded. The same generated `observability.js` is also loaded by public pages.
- Python API: every GET/POST route, status, duration and propagated W3C trace ID.
  Daily and structured-model calls are child spans. Unexpected API exceptions,
  5xx domain errors, worker failures and uncaught thread failures produce logs.
- Outreach CPU, RAM, load and filesystem metrics every minute. Local API/body
  and website checks, systemd state for md-*, HH, TG and shared Postgres/Redis
  produce service-up gauges and transition logs. No messages are sent.

No request/response bodies, auth headers, console, raw exception messages, CVs,
chat text or video/audio are exported. Text and inputs are masked; private DOM
attributes, URL query/hash and dynamic route IDs are removed before export.
Images, iframes, canvases, contenteditable and `[data-private]` are blocked.
The browser exporter wrapper is required: the documented attribute callback in
`@hyperdx/otel-web@0.20.0` is not called by its default exporter. A rrweb plugin
sanitizes full snapshots and incremental mutations before serialization.

This finds instrumented failures; it does not detect every business logic bug.
Legacy standalone md-*/TG processes do not yet have application spans. External
uptime monitoring and synthetic business scenarios remain separate work.
A host outage also makes this colocated monitor unavailable. Browser stacks have
bundle positions; private source map upload/symbolication is not configured.

## Capacity and persistence

Initial retention is **24 hours** for all signals. Limits: ClickHouse 768 MiB,
HyperDX 640 MiB, MongoDB 384 MiB, collector 160 MiB. Docker logs rotate at 2 x10 MB.
This is a small single-host deployment, without HA. Keep retention short until
outreach has a larger data disk. Time-based TTL is not a strict volume cap.

State: `/var/lib/mashadasha-observability/`; private environment:
`/etc/mashadasha-observability/app.env` (0600); deployment:
`/opt/mashadasha-observability/`. Never put credentials or data into releases.
`guard.py` stops ONLY the collector below 1700 MiB free and resumes above
1900 MiB after TTL cleanup. ClickHouse reserves another 1.5 GiB internally.
`guard-status.json` reports whether collection is paused. When paused, telemetry
can be lost; application requests continue. The Python exporter is bounded and
best effort (256 queued records, 2-second transport timeout, no product retries).

## Install / update

1. Copy this directory to `/opt/mashadasha-observability` on outreach.
2. Run `sudo python3 provision.py`, then `sudo docker compose up -d`.
3. Before publishing the panel, register its first owner via loopback API
   `/register/password` with email/password/confirmPassword. Store credentials
   privately. Default Sources provision Logs, Traces, Sessions and Metrics.
4. Run `sudo python3 install-origin.py` on outreach. It validates/reloads the
   existing product Caddy config and installs the disk guard. It preserves all
   current routes and restores the config on validation failure.
5. Copy `public-panel.caddy` to `/tmp/mashadasha-observability.caddy` on dsa and
   run `install-public.py`. It adds only the HTTPS reverse proxy.
6. Install the reviewed `../ci/receiver.py` root-owned at
   `/usr/local/libexec/hr-vision-ci/receiver.py`. Old complete runtime layouts
   remain accepted. Add an API systemd drop-in:
   `Environment=HR_OTEL_ENDPOINT=http://127.0.0.1:8418` under `[Service]` and run
   `systemctl daemon-reload`; the normal CI deploy performs the restart with its
   active-call guard and SQLite backup. No new runtime pip dependencies.
7. Merge the instrumentation through the normal main CI pipeline. Then run
   `enable-public-pages.py` on outreach to add the loader to a copy of the current
   branded site without rebuilding/reverting its assets.

Native API paths are exposed under `/api/*` on the panel. Public telemetry accepts
POST only on `/telemetry/v1/{traces,logs}` with a production Origin and a 2 MB body
limit. Origin is an abuse filter, not a secret or user identity; the endpoint is
write-only and cannot query data. Do not publish collector/database ports.

## Verify / rollback

`docker compose ps`; real browser error and session replay; query ClickHouse for
the same `rum.sessionId` and TraceId on browser/API spans; check unrelated product
health, `free -m`, `df -h /` and `guard-status.json`. A 200 response alone is not
acceptance: open the authenticated panel and play an ingested session.

Disable API export by removing its drop-in at the next guarded API restart.
Stop browser collection by serving an empty `observability.js` at the origin
before the static handler. `docker compose stop` stops this stack only, without
removing volumes. Restore Caddy backups only after checking subsequent changes;
never replace unrelated current routes. The public-page loader can be reverted
via `previous-observability` after checking that nobody has since deployed pages.
