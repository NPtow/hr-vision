export const CTA_OFFER_SECONDS = 40;

// Both the website clock and the lab player use the same fixed offer phases.
export function ctaSnapshot(progress: number) {
  const p = Number.isFinite(progress) ? Math.max(0, Math.min(1, progress)) : 0;
  const seconds = Math.max(0, Math.min(30, Math.ceil((1 - p) * CTA_OFFER_SECONDS - 1e-9)));
  const expired = p === 1;
  const discount = expired ? 0 : p < .25 ? 50 : 30;
  return { seconds, discount, expired, timerVisible: p >= .25 && !expired };
}

// Derive time from a monotonic timestamp, never accumulated animation frames.
export function ctaProgressAt(elapsedMs: number) {
  return Number.isFinite(elapsedMs) ? Math.max(0, Math.min(1, elapsedMs / (CTA_OFFER_SECONDS * 1000))) : 0;
}
