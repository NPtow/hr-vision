export type BookingAttempt = { token: string; slotId: string; requestId: string; email: string; consentProfile: true };

export function restoreBookingAttempt(serialized: string, token: string): BookingAttempt | null {
  try {
    const value = JSON.parse(serialized) as BookingAttempt;
    return value?.token === token && typeof value.slotId === 'string' && typeof value.requestId === 'string'
      && typeof value.email === 'string' && value.consentProfile === true ? value : null;
  } catch { return null; }
}

/** Reuse the exact command after a lost response; editing a command creates a new identity. */
export function bookingAttempt(previous: BookingAttempt | null, token: string, slotId: string, email: string, id: () => string): BookingAttempt {
  const normalized = email.trim().toLowerCase();
  return previous?.token === token && previous.slotId === slotId && previous.email === normalized
    ? previous : { token, slotId, email: normalized, consentProfile: true, requestId: id() };
}
