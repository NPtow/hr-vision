import { adaptEngineCandidateProfile } from './assessment-adapter.ts';
import type { AssessmentAdapterContext, EvidenceLocator } from './assessment-adapter.ts';
import { canonicalSources, engineGolden, resolvedFixtureExcerpts } from './engine-v1-sales-b2b-partial.ts';

export { engineGolden as demoEngineProfile, canonicalSources as demoSources };

export const demoFixtureProvenance = {
  synthetic: true,
  repository: 'sudo-sane/coverletterbot',
  commit: 'c978f1198d35b147510757a55065d05c034bf26d',
  golden: 'services/assessment-service/testdata/engine/v1/goldens/scripted/profile-sales_b2b-partial.json',
  quoteResolution: 'Source and quote hashes checked when copying the fixture and by the Node tests; not browser runtime hash verification.',
  videoMapping: 'Host-authored synthetic 20-second preview: turn 2 at 2s, turn 4 at 8s, turn 7 at 15s. No engine timestamps.',
} as const;

const transcriptSource = canonicalSources.find(item => item.kind === 'interview_transcript')!;
const timeline = new Map<number, number>([[2, 2], [4, 8], [7, 15]]);

export function resolveDemoSeconds(locator: EvidenceLocator): number | null {
  if (locator.sourceId !== transcriptSource.sourceId || locator.sourceSha256 !== transcriptSource.sha256) return null;
  const raw = engineGolden.body.evidence.find(item => item.evidence_id === locator.evidenceId);
  if (!raw || raw.source_id !== locator.sourceId || raw.quote_sha256 !== locator.quoteSha256
    || raw.byte_from !== locator.byteFrom || raw.byte_to !== locator.byteTo
    || raw.turn_ids.length !== locator.turnIds.length || raw.turn_ids.some((id, index) => id !== locator.turnIds[index])) return null;
  // A supplied exact turn mapping only. No interpolation or transcript-order guesses.
  const values = locator.turnIds.map(id => timeline.get(id));
  return values.length > 0 && values.every((item): item is number => item !== undefined) ? Math.min(...values) : null;
}

export const demoAdapterContext: AssessmentAdapterContext = {
  familyId: 'sales_b2b',
  applicationId: 'demo-application-sales-b2b-partial',
  vacancyId: 'demo-vacancy-sales',
  vacancyTitle: 'Менеджер по продажам',
  candidate: {
    name: 'Мария Соколова',
    role: 'Менеджер B2B-продаж',
    city: 'Не указано',
    salary: 'Не указано',
    experience: 'Учебный профиль',
    contact: 'maria.sokolova@example.test',
    resumeText: 'Синтетический профиль для макета.\n\nУчебный соискатель выполнял задачи в вымышленной мастерской «Листосбор». За сентябрь обработал 20 учебных обращений.\n\nГород, зарплатные ожидания и условия выхода не указаны. Имя Мария Соколова используется только как персона демонстрации.',
  },
  criterionOrder: ['A10', 'A07', 'A04', 'A06.1', 'A11.1'],
  // Labels are the pinned criteria-2026-10-v1 catalog names. Summaries describe
  // these exact synthetic source turns, not an invented biography or a score.
  descriptions: {
    A10: {
      label: 'Знание продукта, рынка и продаж; корректность обещаний',
      summary: 'Сравнила два учебных решения и прямо сказала, что для настройки потребуется отдельный специалист.',
      nextQuestion: 'Как вы проверяете обещания клиенту, если детали внедрения ещё не известны?',
    },
    A07: {
      label: 'Продвижение к согласованному следующему шагу',
      summary: 'Предложила пробную сверку в среду, распределила действия и попросила подтвердить следующий шаг.',
      nextQuestion: 'Что вы делаете, если клиент согласен с идеей, но не подтверждает конкретную дату?',
    },
    A04: {
      label: 'Диагностика потребности и бизнеса клиента',
      summary: 'Рассказала, что выяснила объём — 20 заказов, но причины задержек не уточнила.',
      nextQuestion: 'Какие вопросы помогли бы понять причины задержек и их последствия для бизнеса?',
    },
    'A06.1': {
      label: 'Работа с сомнениями и возражениями',
      summary: 'В этом интервью не было отдельного ответа на возражение. Оснований для уровня пока нет.',
      nextQuestion: 'Клиент говорит: «Не хочу менять процесс — сейчас и так всё работает». Как ответите?',
    },
    'A11.1': {
      label: 'Адаптация подхода в ходе разговора',
      summary: 'Изменение подхода после новой информации не обсуждали. Это открытый вопрос, а не слабая сторона.',
      nextQuestion: 'Расскажите о разговоре, в котором новая информация заставила вас изменить подход.',
    },
  },
  sources: canonicalSources,
  resolveExcerpt: locator => resolvedFixtureExcerpts.find(item => item.evidenceId === locator.evidenceId
    && item.sourceId === locator.sourceId && item.sourceSha256 === locator.sourceSha256
    && item.quoteSha256 === locator.quoteSha256 && item.byteFrom === locator.byteFrom && item.byteTo === locator.byteTo) ?? null,
  resolveSeconds: resolveDemoSeconds,
};

/** Ready view-model export; no React view imports the engine fixture directly. */
export const demoAssessment = adaptEngineCandidateProfile(engineGolden, demoAdapterContext);

export const demoTurns: { id: number; speaker: string; text: string; seconds: number | null }[] = transcriptSource.text
  .trimEnd().split('\n').map((line, index) => ({
    id: index + 1,
    speaker: line.startsWith('К:') ? 'Мария' : 'Рекрутер',
    text: line.slice(line.indexOf(':') + 1).trim(),
    seconds: timeline.get(index + 1) ?? null,
  }));
