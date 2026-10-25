import type { CandidateState } from './api';

export type CandidatePage = 'welcome' | 'login' | 'register' | 'resume' | 'preferences' | 'home' | 'calendar' | 'booking' | 'profile' | 'vacancies' | 'interview' | 'meeting';
const pages: CandidatePage[] = ['welcome', 'login', 'register', 'resume', 'preferences', 'home', 'calendar', 'booking', 'profile', 'vacancies', 'interview', 'meeting'];
export const authenticatedCandidatePage = (page: CandidatePage) => !['welcome', 'login', 'register'].includes(page);

export function candidateSetupPage(candidate: CandidateState, newBooking = false): 'resume' | 'preferences' | null {
  // Never hide an appointment or a time-sensitive employer action behind setup.
  if (candidate.interview && !newBooking) return null;
  if (!candidate.resume) return 'resume';
  if (!candidate.resumeReviewed || candidate.onboarding.missingFields.length) return 'preferences';
  return null;
}

export function candidateCompletionStep(candidate: CandidateState): 'resume' | 'preferences' | 'consent' | null {
  if (candidate.interview?.status !== 'completed' || candidate.searching === false) return null;
  return candidateSetupPage(candidate, true) || (!candidate.consentReuse ? 'consent' : null);
}

export function candidateAfterProfile(candidate: CandidateState): CandidatePage {
  return candidateSetupPage(candidate, true) || (candidate.interview ? 'home' : 'booking');
}

export function candidateDestination(candidate: CandidateState, wanted: CandidatePage = 'home'): CandidatePage {
  const pending = candidateSetupPage(candidate, wanted === 'booking' && candidate.interview?.status === 'completed');
  if (pending) return pending;
  if (['resume', 'preferences'].includes(wanted) && !candidateSetupPage(candidate, true)) return candidate.interview ? 'home' : 'booking';
  return ['welcome', 'login', 'register'].includes(wanted) ? 'home' : wanted;
}
export function candidatePage(query: URLSearchParams): CandidatePage {
  const value = query.get('cp') as CandidatePage;
  return pages.includes(value) ? value : 'welcome';
}
export function candidateLoginTarget(query: URLSearchParams): CandidatePage {
  const value = query.get('returnCp') as CandidatePage;
  return pages.includes(value) && authenticatedCandidatePage(value) ? value : 'home';
}
/** Internal route context only; account access is still decided by the session/API. */
export function candidateNavigationURL(href: string, next: CandidatePage, applicationId?: string, meetingId?: string, preserveLoginTarget = false): URL {
  const url = new URL(href);
  if (next === 'login' && preserveLoginTarget) {
    const page = candidatePage(url.searchParams);
    url.searchParams.set('returnCp', authenticatedCandidatePage(page) ? page : candidateLoginTarget(url.searchParams));
    applicationId = url.searchParams.get('application') || undefined;
    meetingId = url.searchParams.get('meeting') || undefined;
  } else url.searchParams.delete('returnCp');
  url.searchParams.set('cp', next); url.searchParams.delete('filter');
  if (applicationId) url.searchParams.set('application', applicationId); else url.searchParams.delete('application');
  if (meetingId) url.searchParams.set('meeting', meetingId); else url.searchParams.delete('meeting');
  return url;
}
