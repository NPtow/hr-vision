# Agency product UI, first slice

Stories: `hr-vision-product--manager`, `hr-vision-product--candidate`.

The first manager entry is a prepared shortlist. The candidate enters a concrete role invitation with materials from an earlier interview. This is a proposed scenario for review, not a released multi-user application.

## Included

- Manager: shortlist, search/filter, candidate evidence, experience/conditions, independent invite/decline/reason, questions, meetings, agreed hiring brief.
- Candidate: role/conditions, interest/decline, editable profile with explicit permission to share updates with this company, human interview request, proposed meeting times, confirmation/reschedule.
- Shared local browser state. Manager interest, candidate interest, and meeting confirmation remain separate. Decline cancels the booking. Reset restores all example data.
- A profile edit without sharing permission is not shown to the manager. Self-reported updates do not automatically rewrite interview evidence.
- Coordinator questions, per-person manager questions, candidate questions, rescheduling preferences, and decline reasons have separate fields.

## UI foundation

Five components are adapted from the official shadcn/ui `new-york-v4` registry: Button, Dialog, Tabs, Input, Textarea. Sources are in `src/components/ui`; MIT license included. Imports are relative to this project. Dialog portal receives the same scoped theme as the application and its close label is localized.

Reference: <https://ui.shadcn.com/docs/theming>. Registry: <https://ui.shadcn.com/r/styles/new-york-v4/button.json> (and corresponding names).

Tailwind 4 Vite plugin builds component utility classes. Preflight is deliberately not loaded; existing maps keep their established styles. Product typography is local Inter Variable with Cyrillic. Theme tokens use colors measured from the supplied presentation, with darker buttons and secondary text for readability.

## Boundaries

All people, quotes, companies, slots, and assessments in this slice are fictional. State is stored in `localStorage` under `hr-vision-agency-preview-v1`; it is not a database or an access-control boundary. Role switch is a review control, not authentication. No actual notification, email, AI/video interview, offer, or calendar integration is performed. Brandpad account/book is not created by this change.

The next production step needs actual users/permissions, company-scoped server state, real evidence and review workflow, coordinator handling, invitation links, and a chosen interview/calendar integration. No claim of production readiness follows from this UI preview.

## Verification

`npm run typecheck` and the deploy script's Storybook build. `scripts/agency-product-check.js` runs via Playwright CLI against either local or public manager story. It checks user-visible transitions and persistence, invitation versus consent, decline/undo, profile validation/sharing, independent questions/rescheduling, keyboard selection, and overflow at 390/768/1024/1440px. Visual screenshots and reports are under `output/playwright/agency-product*` at repo root.
