import type { BoardDocument, NodeKind, Position } from './protocol';

export const CARD_WIDTH = 248;
export const CARD_HEIGHT = 220;

// New cards use free space; existing human positions never move implicitly.
export function vacantPosition(preferred: Position, occupied: Position[]): Position {
  const clear = (p: Position) => occupied.every(q => Math.abs(p.x - q.x) >= CARD_WIDTH + 36 || Math.abs(p.y - q.y) >= CARD_HEIGHT + 36);
  if (clear(preferred)) return preferred;
  for (let ring = 1; ring <= 100; ring++) {
    for (let y = -ring; y <= ring; y++) for (let x = -ring; x <= ring; x++) {
      if (Math.abs(x) !== ring && Math.abs(y) !== ring) continue;
      const p = { x: preferred.x + x * 300, y: preferred.y + y * 272 };
      if (clear(p)) return p;
    }
  }
  return { x: Math.max(preferred.x, ...occupied.map(p => p.x)) + 300, y: preferred.y };
}

// Explicit, undoable command, never an automatic migration.
export function arrangeCards(doc: BoardDocument) {
  const columns: Record<NodeKind, number> = { source: 0, contact: 0, observation: 1, hypothesis: 2, offer: 3, note: 3 };
  const rows = [0, 0, 0, 0];
  return [...doc.nodes].sort((a, b) => columns[a.kind] - columns[b.kind] || a.position.y - b.position.y || a.id.localeCompare(b.id)).map(node => {
    const col = columns[node.kind];
    return { nodeId: node.id, x: 80 + col * 350, y: 100 + rows[col]++ * 292 };
  });
}
