import type { JSONContent } from '@tiptap/core';

export function documentFromText(text: string): JSONContent {
  return {
    type: 'doc',
    content: text.split(/\r?\n/).map(line => ({
      type: 'paragraph',
      ...(line ? { content: [{ type: 'text', text: line }] } : {}),
    })),
  };
}

export function documentText(document: JSONContent): string {
  const blocks: string[] = [];
  function inline(node: JSONContent): string {
    if (node.type === 'hardBreak') return '\n';
    return node.text ?? (node.content || []).map(inline).join('');
  }
  function visit(node: JSONContent) {
    if (['paragraph', 'heading', 'codeBlock'].includes(node.type || '')) blocks.push(inline(node));
    else (node.content || []).forEach(visit);
  }
  visit(document);
  return blocks.join('\n');
}
