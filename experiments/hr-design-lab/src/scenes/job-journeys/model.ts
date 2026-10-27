export type JourneyId = 'contacts' | 'agency' | 'candidate-agency' | 'candidate-other';
export type JourneyDetail = { before: string; action: string; after: string; job: string; ours: string; evidence: string };
export type JourneyBranch = { id: string; question: string; options: { id: string; label: string; result: string }[] };
export type JourneyEvent = JourneyDetail & { id: string; phase: number; kind: string; title: string; effect: string; branch?: JourneyBranch; requires?: { branch: string; value: string }[]; blockedMessage?: string };
export type JourneyJob = { id: string; title: string; note: string; from: number; to: number; detail: JourneyDetail; when?: { branch: string; value: string }; empty?: string; hideUntilActive?: boolean };
export type JourneyLane = { id: string; title: string; goal: string; goalNote: string; goalFrom?: number; goalBefore?: string; quote?: string; goalDetail: JourneyDetail; jobs?: JourneyJob[]; events: JourneyEvent[]; continuation?: string };
export type JobJourneyData = { id: JourneyId; title: string; subtitle: string; entry: string; boundary: string; phases: string[]; lanes: JourneyLane[]; hypothesis: string; risk: string; test: string; uncertainties: string[]; };
