# Public candidate profiles

The existing candidate story supports `cp=public#profile=<capability>`. It renders
the shared candidate card, original recording, transcript and original CV, with
the HR Vision logo and site link. It does not load a candidate account session.
The public adapter supplies no recruiter notes, hiring actions or assessments.

`PublicProfiles` binds a random 256-bit capability to one completed initial
interview and its saved room. Only its SHA-256 is stored. API calls use JSON POST
and the capability never appears in the API URL. HTML metadata prevents indexing
and referrer forwarding; API responses are no-store. Request body capture must
remain disabled in observability.

An authenticated candidate can POST `/api/hr/candidate/public-profile` with
`{"action":"create","consent":true}`. Creation rotates any previous capability;
the response returns the URL once. GET reports active status without recovering
the token. POST `{"action":"revoke"}` disables the owner's capability. Creation
does not modify `consentReuse` or automatic hiring preferences.

For an explicitly approved operator publication, call `PUBLIC_PROFILES.issue`
inside a database transaction with the selected candidate ID, an attributable
`issuer` and an allowed product origin. Never insert tokens into fixtures, static
builds, logs or version control. The same `revoke` operation disables operator
grants. Do not infer an operator approval from a missing consent flag.

Revocation is checked before every read and again after material-provider I/O.
The page revalidates on focus and every minute while visible. Already downloaded
documents cannot be recalled, and an issued storage URL remains valid until its
short provider expiry. Replacing the interview/room invalidates the capability.

Deployment adds `public_profiles.py` to the API package. Install the reviewed
receiver before publication; `PRE_PUBLIC_PROFILE_RUNTIME` keeps the previous
complete archive accepted. The additive SQLite table is not deleted on rollback;
an old runtime cannot serve public capabilities. All normal live-call guards and
database backup checks still apply.

Validation: `python3 -m unittest -v test_public_profiles` from `server`, receiver
tests from `deploy/ci`, typecheck, service build and an anonymous browser check.
Browser acceptance includes video playback/seek, CV disclosure, mobile layout,
invalid/revoked links, and absence of assistant/hiring/comment controls.
