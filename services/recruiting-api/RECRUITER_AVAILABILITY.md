# Availability by date

The recruiter profile offers a date calendar and the existing weekly template.
Each selected date can have several intervals or no interviews. Date settings
replace that recruiter's weekly availability on that Moscow calendar date.
Returning to the weekly template removes only the override, never a booking.

## Reused component and research

Context7 was queried before implementation: `/gpbl/react-day-picker` (single
selection, Russian locale, time zones and custom date modifiers) and
`/websites/cal` (date-specific availability overrides).

- Calendar: `@daypicker/react` 10.0.2, MIT, installed with a pinned version and
  lockfile. [Source](https://github.com/gpbl/react-day-picker),
  [time zones](https://daypicker.dev/docs/time-zone).
- Interaction reference: [Cal availability date overrides](https://cal.com/docs/atoms/availability-settings).
  No Cal service, API dependency, account or AGPL implementation is embedded.
- `TZDate` and `timeZone="Europe/Moscow"` keep the selected calendar date stable
  in browsers outside Moscow. Styling follows the existing MashaDasha surfaces.

## Contract

Authenticated `POST /api/hr/recruiter/availability/date`:

```json
{
  "date": "2026-10-15",
  "intervals": [
    {"startTime": "09:00", "endTime": "10:00"},
    {"startTime": "18:00", "endTime": "19:00"}
  ]
}
```

`intervals: []` closes a date; `intervals: null` restores the weekly template.
Owner is derived from the active recruiter session, never accepted from input.
Only today through 370 days ahead is editable. Times use Moscow, a 30-minute
grid, same-day intervals of 30 minutes through 12 hours, at most 24 intervals.
Overlaps and extra input keys are rejected before any write.

`availabilityDates` in recruiter state returns only the current recruiter's
overrides. Both the candidate portal and personalized HH booking use the same
slot pool and policy, including the final booking write under the existing
transaction/lock. A stale link to a closed time returns 409.

`cp_availability_dates` stores settings. `cp_date_generated_slots` preserves
provenance even after restoring the weekly template. Historical rows and foreign
keys remain intact; retired free slots are hidden and rejected on booking.
Booked meetings stay visible to the recruiter and existing booking retries are
idempotent. Overlapping retained manual/date/weekly rows yield only one free
slot; none can overlap an existing commitment.

## Verification

`test_recruiter_availability` covers replacement/restoration, multiple intervals,
date-only schedules, midnight in Moscow, stable repeated writes, retained booked
and manual slots, cancellation, rollback, overlap protection and validation.
`test_recruiter_access` exercises authenticated API writes, owner isolation,
candidate slot listing, stale booking rejection and successful booking after
reopening. All use isolated SQLite and synthetic people, without delivery.

Local browser QA covers failed save/retry without losing input, closed dates,
restore, cancel/focus, weekly navigation, and 1280/375/320px layouts. It uses a
stateful fixture; production deployment and real recruiter acceptance are
separate checks.
