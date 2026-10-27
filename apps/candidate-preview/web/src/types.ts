/** Source fields remain unmodified; optional values are never filled with demo data. */
export type SourceObject = Record<string, unknown>;
export type Candidate = Record<string, any> & { id: string; name?: string; desiredRole?: string; city?: string; about?: string };
export type SelectionPerson = Candidate;
export type Directory = { candidates: Candidate[]; count: number };
export type DirectoryPage = Directory & { offset: number; limit: number; nextOffset: number | null };
export type Readiness = SourceObject & { candidateId: string; assessment?: SourceObject & { state?: string; coreRun?: SourceObject; result?: unknown }; materials?: SourceObject };
export type InterviewPart = SourceObject & { id: string; recordingId?: string; kind?: string; state?: string; partIndex?: number; transcriptStatus?: string; transcriptId?: string | null };
export type Interview = SourceObject & { id: string; status?: string; createdAt?: string | null; completedAt?: string | null; parts: InterviewPart[] };
export type InterviewCatalogue = SourceObject & { candidateId: string; interviews: Interview[] };
export type Recording = SourceObject & { id: string; url?: string; kind?: string };
export type Transcript = SourceObject & { id: string; recordingId?: string; text?: string };
export type Materials = SourceObject & { candidateId: string; interviewId: string; parts: InterviewPart[]; recordings: Recording[]; transcripts: Transcript[]; partTranscripts?: Transcript[] };
