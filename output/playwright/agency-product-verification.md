# Agency UI verification · 2026-10-03

- Local: `http://127.0.0.1:6007/iframe.html?id=hr-vision-product--manager&viewMode=story`.
- Product stories: manager and candidate. Existing stories retained.
- TypeScript and Storybook production build passed.
- Chrome interaction run: 49 assertions passed. Covers selection/search, sourced evidence, dialog dismissal, refusal reason validation, questions, profile consent, independent interest, booking gating, keyboard slot selection, persistence, rescheduling, refusal, cancellation, and four viewport widths.
- Static independent review: two early defects fixed (general coordinator question attached to last candidate; profile updates not reaching manager). No new material behavior defects in final review.
- Previous agency prototype, agency CJM, XPM: browser smoke passed with zero page errors. New XPM link is usable at 390px; no header overflow.
- Screenshots: `agency-product-manager-first.png`, `agency-product-candidate-first.png`, `agency-product-manager-mobile.png`, `agency-product-candidate-mobile.png`.

## Initial publication attempt

Deployment built successfully but stopped before upload at disk-reserve check: 406224 KiB available, 539760 KiB required. Existing public release remains `20261003T114512Z-20165`. Subsequent read-only inspection showed space falling to 259100 KiB. Docker reported no reclaimable images, containers, volumes, or build cache. ClickHouse log volume was 1.5 GiB; its error log was 404184232 bytes, last modified 2026-10-02. Requested approval to preserve a local backup and clear that specific log. No cleanup or service changes performed without that approval.

These checks demonstrate the UI preview, not multi-user production readiness or real interview/calendar/message integration.


## Public verification · 2026-10-04

Published source commit `40e9897` as release `20261004T063901Z-32590` on `https://hr-vision.158-160-179-53.sslip.io`. Previous release: `20261003T114512Z-20165`.

- TypeScript, production build and deploy file-hash checks passed. Public index contains 34 stories.
- Public Chrome run: all 49 interaction assertions passed; zero browser console errors or warnings.
- Direct entry to manager and candidate stories verified. Desktop and mobile screenshots captured at 1440 and 390 px; no horizontal overflow. Regression run also covers 768 and 1024 px.
- Report: `agency-product-check-public-20261004.json`. Screenshots: `agency-product-public-{manager,candidate}-{1440,390}-20261004.png`.

Deployment initially encountered a full shared server disk. A closed, unused ClickHouse `.log.0` archive was backed up locally, verified by SHA-256, then replaced with a losslessly compressed copy on the server. Original contents were verified again after restoration. No active log was truncated; database data, services and retention were unchanged. Private backup and verification manifests remain under ignored `tmp/yc-log-archive-20261004/`, outside the repository and public build.

After publication, the shared disk filled again and ClickHouse reported unhealthy. Fresh manager and candidate HTTP checks still returned 200. This publication does not resolve the separate disk-growth incident. UI data remains fictional and browser-local; no production backend was added.
