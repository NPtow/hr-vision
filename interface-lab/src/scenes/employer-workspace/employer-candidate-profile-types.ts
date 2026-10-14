export type ScreeningComment = {
  id: string; interviewId: string; recordingId: string | null; seconds: number;
  chapterTitle?: string; text: string; createdAt: string;
};
export type EmployerRejectionValues = {
  reason: string; missing: string; improve: string; expectedRevision: number; requestId: string;
};
export type EmployerCandidateProfileSource = {
  score?: number | null; role?: string; interviewRole?: string;
  profile?: { label: string; value: string }[]; cv?: string;
  contact?: string;
  contacts?: { kind: 'email' | 'phone' | 'telegram'; value: string }[];
};
export type EmployerCandidateResume = {
  id: string; name: string; sizeLabel?: string;
  load: (signal: AbortSignal) => Promise<Blob>;
  onDownload?: () => void;
};
