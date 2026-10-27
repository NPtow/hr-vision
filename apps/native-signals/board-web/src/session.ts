import type { NodeFields } from './protocol';
export type PendingCommand = { type: 'transaction' | 'undo' | 'redo'; payload: Record<string, unknown> & { transactionId: string } };
export function isAcknowledged(pending: string | undefined, ack: string | undefined): boolean { return Boolean(pending && ack && pending === ack); }
export type Draft = NodeFields & { baseline: NodeFields; conflict: boolean };
export function draftFor(fields: NodeFields): Draft { return { ...fields, baseline: { ...fields }, conflict: false }; }
export function draftIsDirty(draft: Draft): boolean { return draft.title !== draft.baseline.title || draft.body !== draft.baseline.body || draft.status !== draft.baseline.status; }
export function reconcileDraft(draft: Draft, canonical: NodeFields, _focused: boolean): Draft {
  const next: Draft = { ...draft, baseline: { ...draft.baseline } };
  let changed = false;
  let conflict = false;
  for (const field of ['title', 'body', 'status'] as const) {
    const incoming = canonical[field];
    const base = draft.baseline[field];
    const local = draft[field];
    if (incoming === base) continue;
    changed = true;
    // Only fields actually edited by the human stay local. Independent native
    // changes are adopted so a later Save cannot silently restore old values.
    if (local === base || local === incoming) {
      Object.assign(next, { [field]: incoming });
    } else {
      conflict = true;
    }
    Object.assign(next.baseline, { [field]: incoming });
  }
  if (!changed) return draft;
  next.conflict = conflict || (draft.conflict && draftIsDirty(next));
  return next;
}
