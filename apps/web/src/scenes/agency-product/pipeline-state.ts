import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import type { CandidateId } from './model';

export type { CandidateId } from './model';

export const PIPELINE_STORAGE_KEY = 'hr-vision-employer-pipeline-v1';
export const PIPELINE_RESET_EVENT = 'hr-vision-pipeline-reset';

export type PipelineView = 'tasks' | 'shortlist' | 'meetings' | 'meeting' | 'feedback' | 'review' | 'pool' | 'offer' | 'complete';
export type MeetingStatus = 'none' | 'pending' | 'confirmed' | 'cancelled' | 'completed';
export type OfferStatus = 'draft' | 'sent' | 'declined' | 'accepted';
export type CandidatePipelineState = {
  meeting: { status: MeetingStatus; date: string; time: string; simulation: boolean };
  feedback: {
    example: string; doubts: string; check: string;
    noExample: boolean; noDoubts: boolean; noCheck: boolean; savedAt: string;
  };
  decision: 'review' | 'pool' | 'declined';
  declineReason: string;
  offer: {
    status: OfferStatus; role: string; compensation: string; format: string;
    startDate: string; expectations: string; prepared: boolean; sentAt: string;
  };
};
export type PipelineState = {
  version: 1;
  view: PipelineView;
  selected: CandidateId;
  byCandidate: Record<CandidateId, CandidatePipelineState>;
};

const candidateIds: readonly CandidateId[] = ['anna', 'mikhail', 'elena'];
const views: readonly PipelineView[] = ['tasks', 'shortlist', 'meetings', 'meeting', 'feedback', 'review', 'pool', 'offer', 'complete'];
const meetingStatuses: readonly MeetingStatus[] = ['none', 'pending', 'confirmed', 'cancelled', 'completed'];
const offerStatuses: readonly OfferStatus[] = ['draft', 'sent', 'declined', 'accepted'];

function freshCandidateState(): CandidatePipelineState {
  return {
    meeting: { status: 'none', date: '', time: '', simulation: false },
    feedback: { example: '', doubts: '', check: '', noExample: false, noDoubts: false, noCheck: false, savedAt: '' },
    decision: 'review',
    declineReason: '',
    // These are editable suggestions, not terms agreed with a candidate.
    offer: {
      status: 'draft', role: 'Менеджер по работе с клиентами', compensation: '',
      format: 'Москва · гибрид', startDate: '', expectations: '', prepared: false, sentAt: '',
    },
  };
}

export function freshPipelineState(): PipelineState {
  return {
    version: 1,
    view: 'tasks',
    selected: 'anna',
    byCandidate: { anna: freshCandidateState(), mikhail: freshCandidateState(), elena: freshCandidateState() },
  };
}

export function canOpenView(pipeline: PipelineState, view: PipelineView, candidateId: CandidateId = pipeline.selected): boolean {
  const candidate = pipeline.byCandidate[candidateId];
  const reviewed = candidate.meeting.status === 'completed' && candidate.feedback.savedAt.trim().length > 0;
  switch (view) {
    case 'tasks':
    case 'shortlist': return true;
    case 'meetings': return true;
    case 'meeting': return candidate.meeting.status !== 'none';
    case 'feedback': return candidate.meeting.status === 'completed';
    case 'review': return reviewed;
    case 'pool': return candidateIds.some(id => {
      const item = pipeline.byCandidate[id];
      return item.decision === 'pool' && item.meeting.status === 'completed' && item.feedback.savedAt.trim().length > 0;
    });
    case 'offer': return reviewed && candidate.decision === 'pool';
    case 'complete': return candidateIds.some(id => pipeline.byCandidate[id].offer.status === 'accepted');
  }
}

type UnknownRecord = Record<string, unknown>;
function record(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function strings(value: UnknownRecord, keys: readonly string[]): boolean {
  return keys.every(key => typeof value[key] === 'string');
}
function oneOf<T extends string>(value: unknown, options: readonly T[]): value is T {
  return typeof value === 'string' && options.includes(value as T);
}

function validCandidate(value: unknown): value is CandidatePipelineState {
  if (!record(value) || !record(value.meeting) || !record(value.feedback) || !record(value.offer)) return false;
  const { meeting, feedback, offer } = value;
  if (!oneOf(meeting.status, meetingStatuses) || !strings(meeting, ['date', 'time']) || typeof meeting.simulation !== 'boolean') return false;
  if (!strings(feedback, ['example', 'doubts', 'check', 'savedAt'])
    || !['noExample', 'noDoubts', 'noCheck'].every(key => typeof feedback[key] === 'boolean')) return false;
  if (!oneOf(value.decision, ['review', 'pool', 'declined']) || typeof value.declineReason !== 'string') return false;
  if (!oneOf(offer.status, offerStatuses)
    || !strings(offer, ['role', 'compensation', 'format', 'startDate', 'expectations', 'sentAt'])
    || typeof offer.prepared !== 'boolean') return false;

  const candidate = value as CandidatePipelineState;
  const savedFeedback = candidate.feedback.savedAt.trim().length > 0;
  if (savedFeedback && candidate.meeting.status !== 'completed') return false;
  if (candidate.decision === 'pool' && !savedFeedback) return false;
  if ((candidate.offer.status === 'sent' || candidate.offer.status === 'accepted')
    && (candidate.decision !== 'pool' || !savedFeedback)) return false;
  return true;
}

export function isPipelineState(value: unknown): value is PipelineState {
  if (!record(value) || value.version !== 1 || !oneOf(value.view, views)
    || !oneOf(value.selected, candidateIds) || !record(value.byCandidate)) return false;
  const byCandidate = value.byCandidate;
  if (Object.keys(byCandidate).length !== candidateIds.length || !candidateIds.every(id => validCandidate(byCandidate[id]))) return false;
  const pipeline = value as PipelineState;
  const activeOffers = candidateIds.filter(id => ['sent', 'accepted'].includes(pipeline.byCandidate[id].offer.status));
  return activeOffers.length <= 1 && canOpenView(pipeline, pipeline.view);
}

function parsePipeline(raw: string | null): PipelineState {
  if (!raw) return freshPipelineState();
  try {
    const value: unknown = JSON.parse(raw);
    return isPipelineState(value) ? value : freshPipelineState();
  } catch {
    return freshPipelineState();
  }
}

function readPipeline(): { pipeline: PipelineState; storageError: boolean } {
  try {
    return { pipeline: parsePipeline(window.localStorage.getItem(PIPELINE_STORAGE_KEY)), storageError: false };
  } catch {
    return { pipeline: freshPipelineState(), storageError: true };
  }
}

export function usePipelineState(): {
  pipeline: PipelineState;
  setPipeline: Dispatch<SetStateAction<PipelineState>>;
  storageError: boolean;
} {
  const [initial] = useState(readPipeline);
  const [pipeline, setPipeline] = useState<PipelineState>(initial.pipeline);
  const [storageError, setStorageError] = useState(initial.storageError);

  useEffect(() => {
    try {
      window.localStorage.setItem(PIPELINE_STORAGE_KEY, JSON.stringify(pipeline));
      setStorageError(false);
    } catch {
      setStorageError(true);
    }
  }, [pipeline]);

  useEffect(() => {
    const reset = () => setPipeline(freshPipelineState());
    const sync = (event: StorageEvent) => {
      if (event.key !== PIPELINE_STORAGE_KEY && event.key !== null) return;
      try {
        if (event.storageArea !== null && event.storageArea !== window.localStorage) return;
      } catch {
        setStorageError(true);
        return;
      }
      const next = parsePipeline(event.key === null ? null : event.newValue);
      setPipeline(current => JSON.stringify(current) === JSON.stringify(next) ? current : next);
    };
    window.addEventListener(PIPELINE_RESET_EVENT, reset);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener(PIPELINE_RESET_EVENT, reset);
      window.removeEventListener('storage', sync);
    };
  }, []);

  return { pipeline, setPipeline, storageError };
}
