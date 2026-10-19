export type CandidatePage = 'welcome' | 'login' | 'register' | 'resume' | 'preferences' | 'home' | 'calendar' | 'booking' | 'profile' | 'vacancies' | 'interview' | 'meeting';
const pages: CandidatePage[] = ['welcome', 'login', 'register', 'resume', 'preferences', 'home', 'calendar', 'booking', 'profile', 'vacancies', 'interview', 'meeting'];
export const authenticatedCandidatePage = (page: CandidatePage) => !['welcome', 'login', 'register'].includes(page);
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
