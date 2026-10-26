import { OutreachError } from '../hh-outreach/api';
import { selectionRequest, type Directory, type SelectionPerson } from './api';
import { hasHRInterview } from './matching-contract';

const PAGE_SIZE = 500;
const incompleteDirectory = 'Не удалось загрузить весь каталог кандидатов. Обновите данные, чтобы повторить загрузку целиком.';
export const MAX_RECOMMENDATION_CANDIDATES = 1000;
export const recommendationPoolLimitMessage = (count: number) => `Для расчёта доступны ${count} кандидатов с HR-интервью. Сузьте пул до ${MAX_RECOMMENDATION_CANDIDATES} кандидатов; расчёт не запущен.`;

/** Resolve only a complete, consistent traversal. Never publish intermediate pages. */
export async function loadCandidateDirectory(token: string, vacancyId?: string): Promise<Pick<Directory, 'candidates' | 'count'>> {
  const candidates: SelectionPerson[] = [], seen = new Set<string>();
  let offset = 0, count: number | undefined;
  try {
    while (true) {
      const query = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE) });
      if (vacancyId !== undefined) query.set('vacancyId', vacancyId);
      const page = await selectionRequest<Directory>('candidates?' + query, token);
      if (!page || !Array.isArray(page.candidates) || !Number.isSafeInteger(page.count) || page.count < 0
        || page.offset !== offset || page.limit !== PAGE_SIZE || (count !== undefined && page.count !== count)) throw new Error('Invalid directory page');
      count = page.count;
      const expectedLength = Math.min(PAGE_SIZE, count - offset);
      if (expectedLength < 0 || page.candidates.length !== expectedLength) throw new Error('Incomplete directory page');
      for (const person of page.candidates) {
        if (!person || typeof person.id !== 'string' || !person.id.trim() || seen.has(person.id)) throw new Error('Missing or repeated candidate ID');
        seen.add(person.id);
      }
      const end = offset + page.candidates.length;
      if (end < count ? !Number.isSafeInteger(page.nextOffset) || page.nextOffset !== end || end <= offset : page.nextOffset !== null) throw new Error('Invalid directory continuation');
      candidates.push(...page.candidates);
      if (page.nextOffset === null) return { candidates, count };
      offset = page.nextOffset;
    }
  } catch (cause) {
    if (cause instanceof OutreachError && [401, 403].includes(cause.status)) throw cause;
    throw new Error(incompleteDirectory, { cause });
  }
}

/** The backend limit is a refusal, never a reason to silently truncate the pool. */
export function recommendationCandidateIds(people: SelectionPerson[]): string[] {
  const ids = people.filter(hasHRInterview).map(person => person.id).sort();
  if (ids.length > MAX_RECOMMENDATION_CANDIDATES) throw new Error(recommendationPoolLimitMessage(ids.length));
  return ids;
}
