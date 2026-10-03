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
