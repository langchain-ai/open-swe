import type { ContentBlock, TextBlock, ToolCallBlock, TrajectoryMessage } from '../types';

function blocks(content: TrajectoryMessage['content']): ContentBlock[] {
  return typeof content === 'string' ? [{ type: 'text', text: content }] : content;
}

function unescapeHtml(text: string): string {
  return text.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&amp;/g, '&');
}

export function messageText(message: TrajectoryMessage): string {
  const text = blocks(message.content)
    .filter((block): block is TextBlock => block.type === 'text' && 'text' in block)
    .map((block) => block.text)
    .join('\n\n');
  if (message.role !== 'human') return text;
  // Open SWE wraps human turns in <input-message …> envelopes with HTML-escaped bodies.
  return unescapeHtml(text.replace(/<input-message[^>]*>\n?/g, '').replace(/\n?<\/input-message>/g, '')).trim();
}

export function toolCalls(message: TrajectoryMessage): ToolCallBlock[] {
  if (message.role !== 'ai') return [];
  return blocks(message.content).filter(
    (block): block is ToolCallBlock => block.type === 'tool_call' && 'name' in block
  );
}

// Open SWE injects <dynamic-context> human turns (identity, repo state) that aren't the user's request.
function isContextOnly(message: TrajectoryMessage): boolean {
  const text = messageText(message);
  return text.startsWith('<dynamic-context') && text.endsWith('</dynamic-context>');
}

function findLastHuman(
  messages: TrajectoryMessage[],
  before: number,
  accept: (message: TrajectoryMessage) => boolean
): number | null {
  for (let index = before - 1; index >= 0; index--) {
    if (messages[index].role === 'human' && accept(messages[index])) return index;
  }
  return null;
}

export type ThreadRow =
  | { kind: 'message'; index: number; message: TrajectoryMessage }
  | { kind: 'gap'; indices: number[] };

export interface FocusedThread {
  rows: ThreadRow[];
  matchCount: number;
  matchIds: Set<string>;
  toolNames: Map<string, string>;
}

/** Keeps the prompting human turn plus every call to `runName` and its result; folds the rest into gaps. */
export function focusThread(
  messages: TrajectoryMessage[],
  runName: string,
  focused: boolean,
  expanded: ReadonlySet<number>
): FocusedThread {
  const matchIds = new Set<string>();
  const toolNames = new Map<string, string>();
  const keep = new Set<number>(expanded);
  let firstMatch = -1;

  messages.forEach((message, index) => {
    for (const call of toolCalls(message)) {
      toolNames.set(call.id, call.name);
      if (call.name !== runName) continue;
      matchIds.add(call.id);
      keep.add(index);
      if (firstMatch < 0) firstMatch = index;
    }
    if (message.role === 'tool' && message.tool_call_id && matchIds.has(message.tool_call_id)) {
      keep.add(index);
    }
  });

  const prompt =
    findLastHuman(messages, firstMatch, (message) => !isContextOnly(message)) ??
    findLastHuman(messages, firstMatch, () => true);
  if (prompt != null) keep.add(prompt);

  const rows: ThreadRow[] = [];
  let gap: number[] = [];
  messages.forEach((message, index) => {
    if (message.role === 'system') return;
    if (!focused || firstMatch < 0 || keep.has(index)) {
      if (gap.length) rows.push({ kind: 'gap', indices: gap });
      gap = [];
      rows.push({ kind: 'message', index, message });
    } else {
      gap.push(index);
    }
  });
  if (gap.length) rows.push({ kind: 'gap', indices: gap });

  return { rows, matchCount: matchIds.size, matchIds, toolNames };
}
