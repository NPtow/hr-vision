# HR Vision · рабочее место рекрутера

`RecruiterPortal` is the minimal operator bridge for the personal candidate path. It is not a replacement ATS and does not create its own candidate personas.

Entry: `iframe.html?id=hr-vision-product--manager&viewMode=story&workspace=recruiter`.

## Access

The browser must have the existing private HR Vision team grant. Only after `/api/hr/access` succeeds does the component create an existing `manager` session. Every `/api/hr/recruiter/*` request is separately authorized by the backend. A public candidate account or its cookie does not grant recruiter access. The public registration link contains no team key or token.

## Supported operations

- Read actual registered candidates and their booked recruiter interview.
- Add real 30-minute recruiter slots, view their booking, and open the candidate profile.
- Create a vacancy in the shared HR Vision vacancy store and propose it after the candidate's completed first interview and reuse consent.
- Read the candidate's optional auto-approval setting. Independently propose a manager meeting from the candidate's submitted availability; the meeting remains pending until the candidate confirms it.
- Join private Daily rooms as owner. Start recording and Russian transcription only after joining; display provider-confirmed recording status and errors.
- Explicitly complete an interview or meeting through the server, then refresh the candidate state.
- Retrieve processed provider recordings and transcripts after a first interview.
- Save internal summary/evaluation separately from the candidate's editable profile and preferences.

## Files

- `RecruiterPortal.tsx`: shell and functional screens; named export `RecruiterPortal`, no props.
- `RecruiterCall.tsx`: Daily owner call, completion and cleanup.
- `api.ts`: typed operator contract, 30-minute availability options, Moscow date conversion.
- `recruiter.css`: isolated `rp-*` layout; existing HRDS font/button/background tokens.

## Verification boundary

`npm run typecheck` passes. This component does not seed fake appointments, completion states or vacancies. Real provider calls and media processing still require configured Daily credentials and a two-party browser verification in the integrated environment. Backend returns timing/permission/provider failures visibly.

Creating slots or vacancies is intentionally append-only in this first operator pass; no editing/deletion endpoint exists yet. Exception handling and schedule changes are operated manually. The matching kernel and HH processing are separate integrations, not simulated by this interface.
