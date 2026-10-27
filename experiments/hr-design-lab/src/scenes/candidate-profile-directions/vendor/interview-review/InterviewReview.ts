import type {ReactNode} from 'react';
import type {InterviewAssistantRequest} from './interview-assistant-api';
import type {AssessmentPresentation} from '../interview-assessment/types';
type InterviewMedia={src:string;label?:string;localPreview?:boolean};
export type RecordingChapter = {
  /** Parsed speech is not a thematic chapter, even when reused by legacy views. */
  kind?: 'transcript';
  title: string;
  detail: string;
  fragment: string;
  source: string;
  seconds: number | null;
  endSeconds?: number | null;
  isQuote?: boolean;
  speaker?: string;
};

export type RecordingChapterContext = { sourceId: string; mode: 'short' | 'full'; index: number };
export type RecordingReviewProps = {
  sourceId: string;
  appearance?: 'panel' | 'candidate';
  recruiterContent?: ReactNode;
  /** Optional identity and document actions, composed by the authorised candidate view. */
  header?: ReactNode;
  name: string;
  chapters: RecordingChapter[];
  kind: 'screening' | 'meeting';
  initialEvidenceIndex?: number;
  compact?: boolean;
  media?: InterviewMedia;
  summary?: string;
  unknown?: string;
  transcriptText?: string;
  /** False when transcript timestamps have not been mapped to this recording. */
  transcriptSeekable?: boolean;
  recordingId?: string;
  onEvidenceNavigate?: (evidence: import('../interview-assessment/types').AssessmentEvidence, criterionId: string) => boolean;
  evidenceRequest?: { recordingId: string; seconds: number; requestId: string;
    evidence?: import('../interview-assessment/types').AssessmentEvidence; criterionId?: string };
  emptyMessage?: string;
  fullChapters?: RecordingChapter[];
  shortPlaylist?: boolean;
  renderChapterComment?: (chapter: RecordingChapter, context: RecordingChapterContext) => ReactNode;
  renderTimeComment?: (seconds: number, onClose: () => void, openRequest?: number) => ReactNode;
  commentTimestamps?: { seconds: number; count: number }[];
  comments?: { id: string; seconds: number; text: string; createdAt: string }[];
  canComment?: boolean;
  /** Public profiles retain review modes but do not ask for fragment feedback. */
  hideFeedbackQuestions?: boolean;
  assistant?: InterviewAssistantRequest;
  /** Only an authenticated adapter may supply a ready assessment and persistence. */
  assessment?: AssessmentPresentation;
  assessmentStatus?: 'loading' | 'unavailable';
};

