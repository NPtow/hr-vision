import type { AssessmentCriterion, AssessmentEvidence, CandidateAssessmentView } from './assessment-view.ts';

/** Only this adapter knows an engine wire shape. React consumes assessment-view.ts. */
export type EngineSource = {
  sourceId: string;
  kind: string;
  sha256: string;
  text: string;
};

export type EngineEvidence = {
  evidence_id: string;
  source_id: string;
  byte_from: number;
  byte_to: number;
  quote_sha256: string;
  turn_ids: readonly number[];
};

type EngineCriterion = {
  criterion_id: string;
  state: string;
  level: number | null;
  basis: string | null;
  flags: readonly string[];
  missing_reason: string | null;
  failed_processing: boolean;
  citations: readonly { evidence_id: string }[];
};

export type EngineCandidateProfile = {
  engine_contract: string;
  format: string;
  document_sha256: string;
  synthetic: boolean;
  body: {
    candidate_id: string;
    evidence: readonly EngineEvidence[];
    families: readonly {
      family_id: string;
      criteria: readonly EngineCriterion[];
    }[];
  };
};

export type EvidenceLocator = {
  evidenceId: string;
  sourceId: string;
  sourceSha256: string;
  quoteSha256: string;
  byteFrom: number;
  byteTo: number;
  turnIds: readonly number[];
};

export type ResolvedExcerpt = Omit<EvidenceLocator, 'turnIds'> & { text: string };

export type CriterionPresentation = {
  label: string;
  summary: string;
  nextQuestion: string;
};

export type AssessmentAdapterContext = {
  familyId: string;
  applicationId?: string;
  vacancyId?: string;
  vacancyTitle: string;
  candidate: CandidateAssessmentView['candidate'];
  criterionOrder: readonly string[];
  descriptions: Readonly<Record<string, CriterionPresentation>>;
  sources: readonly EngineSource[];
  /** Host resolves exact canonical bytes/hash under its own source access rules. */
  resolveExcerpt: (locator: EvidenceLocator) => ResolvedExcerpt | null;
  /** Host-owned scoped mapping; the kernel does not contain media timestamps. */
  resolveSeconds: (locator: EvidenceLocator) => number | null;
};

function basisOf(value: string | null): AssessmentCriterion['basis'] {
  return value === 'observed' || value === 'narrated' || value === 'hypothetical' || value === 'reflected' ? value : null;
}

function stateOf(value: string): AssessmentCriterion['state'] {
  return value === 'supported' || value === 'conflict' || value === 'not_applicable' ? value : 'unknown';
}

function timeLabel(seconds: number | null): string {
  if (seconds === null) return '';
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}

function sameLocator(resolved: ResolvedExcerpt, locator: EvidenceLocator): boolean {
  return resolved.evidenceId === locator.evidenceId
    && resolved.sourceId === locator.sourceId
    && resolved.sourceSha256 === locator.sourceSha256
    && resolved.quoteSha256 === locator.quoteSha256
    && resolved.byteFrom === locator.byteFrom
    && resolved.byteTo === locator.byteTo;
}

/** Pure presentation adapter: no provider calls, hiring decisions, inferred times or total score. */
export function adaptEngineCandidateProfile(profile: EngineCandidateProfile, context: AssessmentAdapterContext): CandidateAssessmentView {
  if (profile.engine_contract !== 'engine-v1' || profile.format !== 'candidate_profile/1') {
    throw new Error('Unsupported assessment contract');
  }
  const family = profile.body.families.find(item => item.family_id === context.familyId);
  if (!family) throw new Error('Assessment family is unavailable');
  const sources = new Map(context.sources.map(item => [item.sourceId, item]));
  const evidenceById = new Map(profile.body.evidence.map(item => [item.evidence_id, item]));

  const resolveEvidence = (id: string): AssessmentEvidence | null => {
    const raw = evidenceById.get(id);
    if (!raw) return null;
    const source = sources.get(raw.source_id);
    if (!source || raw.byte_from < 0 || raw.byte_to <= raw.byte_from) return null;
    const locator: EvidenceLocator = {
      evidenceId: raw.evidence_id, sourceId: source.sourceId, sourceSha256: source.sha256,
      quoteSha256: raw.quote_sha256, byteFrom: raw.byte_from, byteTo: raw.byte_to, turnIds: raw.turn_ids,
    };
    const resolved = context.resolveExcerpt(locator);
    if (!resolved || !sameLocator(resolved, locator) || !resolved.text.trim()) return null;
    const sourceBytes = new TextEncoder().encode(source.text);
    if (raw.byte_to > sourceBytes.byteLength) return null;
    try {
      if (new TextDecoder('utf-8', { fatal: true }).decode(sourceBytes.slice(raw.byte_from, raw.byte_to)) !== resolved.text) return null;
    } catch {
      // A damaged source range withholds this excerpt, never the whole profile.
      return null;
    }
    const mapped = context.resolveSeconds(locator);
    const seconds = typeof mapped === 'number' && Number.isFinite(mapped) && mapped >= 0 ? mapped : null;
    return {
      id, text: resolved.text, sourceId: source.sourceId,
      turnIds: [...raw.turn_ids], seconds, time: timeLabel(seconds),
      sourceKind: source.kind === 'cv_text' ? 'resume' : 'interview',
      availability: seconds === null ? 'text_only' : 'ready',
    };
  };

  const criteria = context.criterionOrder.flatMap(id => {
    const raw = family.criteria.find(item => item.criterion_id === id);
    if (!raw) return [];
    const copy = context.descriptions[id];
    if (!copy) return [];
    const validLevel = Number.isInteger(raw.level) && raw.level !== null && raw.level >= 1 && raw.level <= 5;
    const originalState = stateOf(raw.state);
    const state = raw.failed_processing || (originalState === 'supported' && !validLevel) ? 'unknown' : originalState;
    const level = state === 'supported' ? raw.level : null;
    const basis = state === 'supported' ? basisOf(raw.basis) : null;
    const evidence = [...new Set(raw.citations.map(item => item.evidence_id))]
      .map(resolveEvidence).filter((item): item is AssessmentEvidence => item !== null);
    const stateLabel = raw.failed_processing ? 'Не удалось обработать'
      : state === 'conflict' ? 'Есть расхождения'
      : state === 'not_applicable' ? 'Вне области оценки'
      : state === 'unknown' ? raw.missing_reason === 'not_asked' ? 'Не обсуждали' : 'Недостаточно данных'
      : basis === 'observed' ? 'Показала в кейсе'
      : basis === 'narrated' ? 'Рассказала об опыте'
      : basis === 'hypothetical' ? 'Описала возможные действия' : 'Поделилась выводом';
    const limitation = raw.failed_processing ? 'Ошибка обработки не является оценкой кандидата.'
      : raw.citations.length > 0 && evidence.length === 0 ? 'Исходный фрагмент сейчас недоступен.'
      : raw.flags.includes('single_episode') ? 'Вывод основан на одном учебном эпизоде.'
      : state === 'unknown' ? 'Отсутствие ответа не означает низкий уровень.'
      : state === 'conflict' ? 'Противоречащие основания не усредняются.' : '';
    return [{
      id, label: copy.label, state, level, target: null, basis, stateLabel,
      summary: copy.summary, nextQuestion: copy.nextQuestion,
      missingReason: raw.missing_reason === 'not_asked' ? 'Эту тему не обсуждали в доступном интервью.' : state === 'unknown' ? 'В доступных материалах пока нет достаточного основания.' : null, limitation, evidence,
    } satisfies AssessmentCriterion];
  });
  return {
    revisionId: profile.document_sha256,
    candidateId: profile.body.candidate_id,
    applicationId: context.applicationId,
    vacancyId: context.vacancyId,
    vacancyTitle: context.vacancyTitle,
    candidate: { ...context.candidate },
    criteria,
  };
}
