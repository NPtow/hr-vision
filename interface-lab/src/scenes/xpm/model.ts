export type XpmStrategyId = 'agency' | 'contacts' | 'ats';

export interface XpmActor {
  id: string;
  name: string;
  /** The person's desired result, separate from situations and interface operations. */
  intention?: string;
}

export interface XpmSource {
  id: string;
  label: string;
  url?: string;
  note?: string;
}

export interface XpmNode {
  id: string;
  actorId: string;
  /** Chronological position inside a local fragment, normally 0–3. */
  column: number;
  label: string;
  kind?: 'key' | 'external' | 'condition';
  owner?: 'ours' | 'shared' | 'external';
  note?: string;
  input: string;
  actions: string[];
  output: string;
  channel?: string;
  tool?: string;
  barrier?: string;
  remedy?: string;
  sourceRefs?: string[];
  evidence?: 'confirmed' | 'hypothesis' | 'decision';
}

export interface XpmEdge {
  id?: string;
  from: string;
  to: string;
  kind?: 'sequence' | 'waiting' | 'sync';
  label?: string;
  delay?: string;
}

export interface XpmChapter {
  id: string;
  title: string;
  result: string;
  nodes: XpmNode[];
  edges: XpmEdge[];
  barrier?: string;
  keyBranch?: string;
  sourceRefs?: string[];
}

export interface XpmStrategy {
  id: XpmStrategyId;
  label: string;
  title: string;
  subtitle: string;
  scope: string;
  actors: XpmActor[];
  chapters: XpmChapter[];
  sources: XpmSource[];
  hypothesis: string;
  risk: string;
  validation?: string;
}
