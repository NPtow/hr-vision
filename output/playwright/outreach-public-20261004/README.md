# HR Vision on outreach · 4 October 2026

Published release: `20261004T174834Z-17891`, UI source `ef49fd8`.

## Delivery

- Origin: outreach `10.130.0.18`, `/srv/hr-vision/current`, isolated `hr-vision-web.service` (Caddy, MemoryMax 128 MiB).
- DSA only proxies HTTPS for the preserved public hostname to outreach port 8381. No HR Vision release was uploaded to DSA in this operation.
- Origin admits localhost and DSA private IP with the expected Host; wrong Host tested as HTTP 403.
- Origin SHA-256 checks passed for all extracted files and required stories. Public index and the gallery/manager JS/CSS match the release manifest; 35 stories.
- DSA active configuration was compared with its saved file before mutation. Only the HR Vision route changed; the other 39 routes/settings were preserved. During comparison, Caddy’s automatically hidden config-file path was normalized from staging to the real pathname.
- DSA configuration backup: `/etc/caddy/Caddyfile.before-hr-vision-outreach-20261004T180413Z`. Graceful reload succeeded; live configuration matched the expected full JSON afterward.
- Old static releases on DSA remain available for routing rollback. New releases and their atomic pointers live on outreach.

## Browser verification

- Public gallery: **91 checks passed**, no page errors, all ten layouts at 1440 and 390 px, profile/evidence navigation, nested Escape/focus restoration, mobile layouts and reduced-motion interaction.
- Public manager: **55 checks passed**, no errors. Task-first entry, context-sensitive meeting navigation and existing connected UI regression coverage.
- Screenshots: `../outreach-gallery-1440.png`, `../outreach-gallery-390.png`, `../outreach-manager-1440.png`.
- Desktop gallery and manager screenshots visually inspected. Public gallery console: 0 errors, 0 warnings.

These are static UI artifacts on fictional data; hosting does not implement interviews, assessments, offers or production storage.
