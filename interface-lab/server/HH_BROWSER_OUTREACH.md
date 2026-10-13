# HH → booking: browser outreach

Implementation, 14 October 2026. The sender uses the existing `hh-daily` browser
owner; it does not call the HH API or open a second Chromium profile.

## Operator flow

Open the existing manager story with `workspace=outreach`. Team access is required;
a recruiter/candidate/employer session does not grant campaign administration.

1. Create a draft: direct resume URLs or applicants of a known HH vacancy,
   message with exactly one `{booking_url}`, total send limit and interval.
2. Supply explicit resume URLs, or the observed HH applicants-list URL (including
   its filters). Import only reads the browser. It prepares profiles and CVs;
   it does not manufacture candidate accounts or consent.
3. Discovery runs in bounded pages of 25 with a durable cursor. Reloading the
   operator screen resumes status polling, not a duplicate discovery request.
4. Review the audience/counts and press Start. Messages and booking URLs are
   frozen in the private durable outbox. A later retry cannot substitute a
   different recipient, message or link.
5. The candidate chooses a 30-minute slot from the active recruiter pool. One
   transaction books the concrete recruiter, attributes the campaign, and puts
   recruiter notifications in the existing outbox. The call link is separate
   and becomes available after booking. Existing recording-consent rules apply.

Known incomplete or restricted CVs and missing chats are skipped, not marked as
sendable. The initial browser adapter never purchases contacts or creates a chat
by clicking an invitation/purchase control. Those actions require a separately
observed UI flow; the current direct mode works with available conversations.

## Main contracts

- `POST /api/hr/booking/state`, `/book`, `/call`: personal capability in JSON,
  not query parameters. Initial URL carries `#booking=...` and the frontend
  removes it immediately. No profile editing, CV download or room token is
  granted by the booking state endpoint.
- `/api/hr/outreach/campaigns`: create/list; `/<id>`: statistics/recipients.
  `/<id>/discover` starts source reading; `/<id>/discovery` returns the durable
  operation. `queue`, `resume`, `pause`, `cancel` are separate operator actions.
- `/api/hr/outreach/browser`: connection/login/calibration/manual state.
  `/browser/resume` explicitly clears a verified browser pause. `/browser/manual`
  coordinates manual control with the old parser and the sender.
- `/api/hr/outreach/jobs/<id>/acknowledge`: leave an uncertain attempt on review,
  without ever allowing another click for it. The operator can then resume
  other jobs; the uncertain candidate and statistics stay uncertain.
- `/api/hr/outreach-worker/*`: separate private bearer, never team/candidate
  credentials. The default in-process scheduler does not need this credential.

## Failure behavior

Both databases persist a barrier before the send click. A timeout/crash after
that point becomes `unknown`. Only observing a new persistent outgoing message
in the exact account/resume/chat with the exact frozen text confirms sending.
DOM acknowledgment is awaited before reloading; the post-reload check is still
required. A click or optimistic UI row never increments the sent counter.

Account lease + browser owner serialize old parsing, discovery, send and manual
work. Logged-out/challenge/changed-page states stop the sender. No challenge
bypass is implemented. Unknown attempts are observed read-only; no automatic
send retry is possible, including after explicit acknowledgment.

Statistics count distinct recipients: sent, page loaded, booked, recruiter-ended
interview, video ready, transcript ready. Page loaded is not evidence of reading
HH; recruiter completion is not proof of two-party attendance. Media must match
the exact current interview room. Bounded background polling retrieves provider
metadata; files stay with the existing media provider.

## Runtime / release

Configuration is documented in `.env.example`. Construction performs no external
calls; both worker and sending switches default to off. Bridge credentials belong
in private environment files. The bridge is loopback-only, authenticated, does
not follow redirects, and never retries mutating requests.

`hh_browser/integration.py` generates an inspectable patch for the existing
worker. Runtime installation is separate from the HR API release. The adapter's
example selector profile is **synthetic and uncalibrated**. It must not be used
as a claimed working HH employer profile. See `hh_browser/README.md`.

The CI receiver needs the complete new runtime allowlist before deploying this
release. Previous complete API layouts remain accepted for rollback. New code
is integrated on top of current main so concurrent employer/candidate changes
are preserved. Only the three existing service stories are shipped.

## Verified boundary

Local verification covers real SQLite transactions, actual HTTP handler access,
booking concurrency, attribution, owner notification enqueue, deduplication,
restart/uncertainty/pause behavior, discovery paging and recovery, isolated
browser DOM fixtures, frontend typecheck and a production Storybook build.
No live HH message is implied by these tests.

Before first real campaign: authorize the actual YC employer browser, calibrate
its current DOM identities/selectors, install the bridge, then verify one chosen
test conversation with the approved text/booking link and a concrete send limit.
On the 14 October audit the browser was logged out. The existing VNC has a
password; the historically documented extra HTTP gate was absent. Inspect its
actual access boundary before putting the employer session there.
