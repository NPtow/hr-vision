# HH source and automatic invitation outbox

`hh_recruiting.py` is a standard-library source/outbox module. `hh_routes.py` connects it to the existing recruiter portal and candidate domain. The recruiter UI has a separate HH panel. It does not install an ATS, publish HH vacancies, search the entire resume base, or implement a chat UI. Both HH access and sending are **off by default**.

Verified contract: official [HH OpenAPI](https://api.hh.ru/openapi/specification/public), retrieved 2026-10-12, SHA256 `0c8eb0bc8a72d63a7ceb7799ff14b0e0017390714a5cad129a72b1340acd7df4`. OAuth uses `https://hh.ru/oauth/authorize` and `POST https://api.hh.ru/token`. Sending uses `POST /common/chats/{chat_id}/messages`; legacy negotiation-message endpoints are not used. No upstream source was copied literally. Research reference `hh-mcp` 0.1.1 / MIT / `69e389a456769156bfc650f677bd0e39f10d5723` is not a runtime dependency.

## App wiring and configuration

Initialize after `CandidatePortal` so its schema is ready. Mount the narrow HH handlers **before** the portal's broad `/api/hr/recruiter/` handler, in both GET and POST. Existing origin checks, private entry grant and `self.actor` Bearer authentication remain in place.

```python
from hh_routes import HHRecruiterRoutes
HH_ROUTES = HHRecruiterRoutes(connect, CANDIDATE_PORTAL)
CANDIDATE_PORTAL.invitation_lookup = HH_ROUTES.existing_invitation

# Within HTTP handler (GET uses None; POST uses the parsed JSON body):
if HH_ROUTES.handles(self.path):
    result, headers = HH_ROUTES.handle(method, self.path, self.headers, body, self.actor)
    self.answer(200, result, headers)
    return

# Once at startup; no import/constructor side effects:
HH_ROUTES.start_worker()  # returns None unless configured + both switches enabled
```

Environment (server only):

- `HR_HH_ENABLED=1`: permits HH OAuth/source API calls.
- `HR_HH_SENDING_ENABLED=1`: independently permits the invitation worker. Default off.
- `HR_HH_CLIENT_ID`, `HR_HH_CLIENT_SECRET`: actual approved application credentials.
- `HR_HH_REDIRECT_URI`: approved **recruiter UI** URL ending with `&hhCallback=1`, e.g. `https://our-host.example/iframe.html?id=hr-vision-product--manager&viewMode=story&workspace=recruiter&hhCallback=1`. Use the actual story identifier and exact registered URI.
- `HR_HH_USER_AGENT`: HH-required application name and actual developer contact.

Invitation origin comes from `CANDIDATE_PORTAL.origin`. No browser secrets/localStorage tokens are added. The UI captures OAuth code/state in transient memory and immediately strips them from the URL before its network effects; authenticated `POST hh/oauth/complete` exchanges them. Do not log callback query strings or request bodies in the web server/proxy/analytics. A missing configuration honestly returns `configured: false`, and all connection/sending controls remain disabled. A successful OAuth connection alone does not prove paid API-method access.

Runtime additions: `server/hh_recruiting.py`, `server/hh_routes.py`; existing `candidate_invites`, `candidate_portal` and `domain` are used without replacement. No packages, service, cron or external infrastructure is required. `start_worker()` is a separate daemon, with a 30-second bounded polling pass, independent of the notifications worker. Alternatively call `HH_ROUTES.process_pending(limit=10)` from an existing scheduler; do not start both unnecessarily. Both forms are no-ops while disabled.

HTTP routes under `/api/hr/recruiter/hh/`, manager-only: GET `state`, `collections`; POST `oauth/start`, `oauth/complete`, `vacancies/sync`, `vacancies/link`, `responses/import`, `rules`, `rules/disable`. DTOs exclude access/refresh tokens, CV contents and invitation capabilities. UI links an explicitly chosen accessible HH vacancy to an existing shared-workspace HR Vision vacancy, then imports explicitly chosen HH response collections. Imports are manual and may be repeated safely; webhooks/background response ingestion are not part of this slice.

Automatic events are only `interview_booked` → call link and `interview_completed` → profile activation link. There is no prefilled audience, event or message. A rule requires explicit vacancy, event, message containing `{invite_url}` and opt-in. A rule edit creates a new version and cancels unsent messages from its predecessor. Only events after enabling are enrolled; old interviews are not bulk-mailed. An existing manually-issued active link is preserved and marked as skipped; multiple eligible HH chats for one person/event are also skipped instead of guessing a recipient. These skipped outcomes appear in the panel.

`invites.issue`, outbox creation and an event ledger are committed in the same SQLite transaction, so a worker restart does not mint a second link. The worker rechecks link expiry/revocation, actual interview state, HH chat identity/permission, active vacancy and enabled rule before posting. No account activation or consent is fabricated.

The `invitation_lookup(db,cid,scope)` read-only callback returns a still-valid HH outbox URL when a recruiter opens/copies the ordinary invitation control. It checks exact person/scope/current interview, expiry, revocation, consumption, URL origin and the fragment token's SHA256 against the candidate invitation. Ordinary copy must not silently revoke a sent link; explicit `replace=True` remains a separate user action in the candidate API.

## Lower-level integration contract

```python
module = HHRecruiting(
    connect,  # Opens a NEW sqlite3 connection on the same private journey DB.
    HHConfig(
        enabled=False,
        sending_enabled=False,
        client_id=server_configuration.client_id,
        client_secret=server_configuration.client_secret,
        redirect_uri="https://our-host.example/iframe.html?id=hr-vision-product--manager&viewMode=story&workspace=recruiter&hhCallback=1",
        user_agent="HR-Vision/1.0 (actual-developer-contact@example.test)",
        invite_origin="https://our-host.example",
    ),
    prepare_candidate=prepare_hh_candidate,
)
```

The example hosts/contact are placeholders, not deployment values. A trusted route must authenticate and authorize the recruiter before passing `actor_id`; never accept it from a request body. Connections are owned by that actor. URLs/codes from an OAuth callback still require the same authenticated actor and the stored single-use state. Work-account/tenant selection beyond one employer manager per connection is not added here.

HH source/delivery state uses new `hh_*` tables. `hh_routes.py` uses existing candidate APIs to create prepared `cp_*` profiles, real CV exports and scoped links. The DB/WAL contain **private tokens, CV facts and invitation URLs**. Use the existing private database directory (0700), restricted backups, and no SQL/request-body logging or public DB exports. Client secrets stay in server configuration; `HHConfig` repr excludes them. Public connection/outbox DTOs exclude tokens, CV, message text and invitation tokens. The default HTTP transport only talks to `api.hh.ru`, refuses redirects, bounds response size and applies timeouts.

Call sequence:

1. `begin_oauth(actor_id)` returns an authorization URL; `finish_oauth(actor_id, state, code)` creates/renews the connection. `check_access(actor_id, connection_id)` retrieves the actual `method_access` body. A connected OAuth identity does not itself prove paid-method access.
2. `sync_vacancies(actor_id, connection_id)` reads all pages of accessible active vacancies. `link_vacancy(..., external_id, local_id)` binds a chosen HH vacancy to an **already authorized existing HR Vision vacancy**. The caller must validate ownership/existence of `local_id`; this module does not own the vacancy domain. Failed/incomplete fetches do not erase the current cached list.
3. `sync_vacancy(..., vacancy_id, collection_ids=[...])` imports explicitly chosen collections. `negotiations(...)` returns their stable source references. Full CV access can be unavailable; that state is explicit, and partial data stays partial. No `topic_id` is added to resume reads. Resume identity is distinct from negotiation identity, and email is never a deduplication key.
4. `configure_rule(..., event_type, template_id, template, enabled=False)` defines an explicit vacancy/event/template rule. Template must contain `{invite_url}`. No audience, event, template or enabled rule is seeded. Existing template IDs are immutable; use a new version for changed copy.
5. Domain code generates an invitation with its own scopes/expiry, then calls `enqueue_invitation(..., rule_id, event_key, invitation_id, url, db=transaction)`. The optional shared transaction makes invitation+outbox atomic. All arguments must come from trusted server state. The same logical event returns the same row; changing its URL/text/chat conflicts instead of silently generating another send. Rule and global worker switches must both be enabled to send.
6. A scheduler chosen by the integrator calls `send_once(actor_id, outbox_id, is_current=guard)` for due rows. It must use the persisted `next_attempt`, not spin. No scheduler starts on import, initialization, OAuth, sync, or enqueue.

## Prepared profile and invitation callbacks

```python
def prepare_hh_candidate(db, external_key, normalized_profile, source_metadata):
    # Same short DB transaction as source upserts. No network/commit/close here.
    # Adapt to candidate_invites.create_prepared(...), owned by the candidate module.
    # Return its stable candidate ID, without creating consent or a fake login.
    ...

def invitation_is_current(db, invitation_id, candidate_id):
    # Read only. Check actual ownership, token expiry/revocation, the approved
    # audience/trigger policy, and current domain state. Return True only if all hold.
    ...
```

Prepared keys: `hh_resume:v1:<connection_id>:<resume_id>`. Profile fields are limited to facts supplied in CV: name, city, desired role, about, employment type. `source_metadata` holds provider, resume ID/URL, unverified contact email, full-info availability and a plain text CV export. It is **not an original PDF**. The adapter must preserve candidate edits on reimport and keep the existing person when activating the account. Callback failure rolls back the whole import transaction.

The candidate module provides `create_prepared(db, profile, source='hh', external_key=..., contact_email=..., cv=...)`. Validate the profile with `candidate_portal.profile_value` and pass an honestly labelled TXT export in `cv={filename, mime, content: bytes, text, fields}` when actual CV data is available. The domain requires a CV to book; missing HH access must request a manual CV, not silently manufacture one. `invites.issue` returns `invitationId`, `url`, `expiresAt`; its `#invite=...` / `#claim=...` token fragments are supported and preserved in the message.

The HH pathway does not require an activated account before interview. The domain owns prepared-profile activation and its timing. Call invitations and later claim invitations are separate domain events; the HH module simply delivers a specifically configured one. Recording/consent logic is outside this module.

## Delivery truth and recovery

- Persisted row and UUID are created before the provider call. A DB lease prevents concurrent workers from posting the same row; lease recovery keeps the same UUID and text.
- HH 201 **with message ID** → `sent` (accepted by HH, not proof of reading).
- Timeout or ambiguous 5xx after POST → `unknown`; bounded retry reuses the same UUID. A timeout before POST → `retry`.
- HH 409 → `needs_review`, never `sent`. Do not invent a message ID or a new UUID to bypass the conflict. A human or later reconciliation feature must resolve unknown outcomes from actual provider evidence; that reconciliation UI is not supplied here.
- Permission/chat errors → `blocked`; invalid request → `failed`; expired/revoked invite or changed recipient → `cancelled`. Five attempts maximum, then `needs_review`.
- Before sending, re-read the negotiation to validate vacancy/chat identity and `messaging_status`, then recheck the rule, vacancy activity and domain guard. The guard supplies the actual audience policy; the module does not assume “every imported candidate”.

OAuth refresh is protected by a DB lease and handles documented `403 token_expired`; revoked tokens require reauthorization. The module does not treat arbitrary 403s as token expiry. A refresh whose outcome was lost may require reauthorization; no expired credential is exposed to clients.

## Verification and remaining boundary

```sh
cd services/recruiting-api
python3 -m unittest test_hh_recruiting test_hh_routes -v
```

52 isolated tests pass on bundled Python 3.12, using synthetic fixtures and temporary SQLite files. They cover OAuth actor/state ownership, token refresh/revocation, page failure, external URL validation, stable import, unavailable/deleted CV fields, transactional callback failure, repeated events, worker leases, cancelled invitations, the domain's fragment-token URLs and delivery outcome/retry rules. Integration tests additionally use the actual CandidatePortal schema and booking API, verify full CV→prepared person without credentials/consent, completed→claim, preservation of manual links, no retroactive sends, ambiguity handling, atomic rollback and safe re-use of an existing sent URL when copying. Frontend `npm run typecheck` also passes.

Still required before live use: root deployment/wiring verification; configured HH application and approved redirect URL; employer authorization and actual API-method access; chosen vacancy/collection/audience/template/event; a permitted test chat; a supervised one-message test. Credentials, OAuth, HH messages and production services were not accessed by this implementation. Source inspection and fixtures do not prove a live HH connector, full CV permission, real delivery or the provider's current account restrictions.
