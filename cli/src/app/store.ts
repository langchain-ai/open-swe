import { create } from 'zustand';
import type { Message, TokenUsage, ToolExecution } from '@types';
import { randomUUID } from 'crypto';

// Seeded as length>=1 so screens that split static/live slices have a stable
// invariant from frame zero. Renderers treat empty system text as a no-op.
const createSeedMessage = (): Message => ({
  id: randomUUID(),
  author: 'system',
  chunks: [{ kind: 'text', text: '' }],
});

type Store = {
  messages: Message[];
  addMessage: (msg: Omit<Message, 'id'>) => void;
  resetMessages: () => void;
  updateToolExecution: (toolExecution: ToolExecution) => void;
  tokenUsage: TokenUsage;
  updateTokenUsage: (usage: TokenUsage) => void;
  busy: boolean;
  setBusy: (busy: boolean) => void;
  blink: boolean;
  toggleBlink: () => void;
  tick: number;
  advanceTick: () => void;
  terminalCols: number;
  terminalRows: number;
  clearRequested: boolean;
};

export const useStore = create<Store>((set) => ({
  messages: [createSeedMessage()],
  addMessage: (msg: Omit<Message, 'id'>) =>
    set((state) => ({ messages: [...state.messages, { ...msg, id: randomUUID() }] })),
  resetMessages: () =>
    set({ messages: [createSeedMessage()], tokenUsage: { input: 0, output: 0, total: 0 } }),
  updateToolExecution: (toolExecution: ToolExecution) =>
    set((state) => ({
      messages: state.messages.map((message) => ({
        ...message,
        chunks: message.chunks.map((chunk) => {
          if (chunk.kind === 'tool-execution' && chunk.toolCallId === toolExecution.toolCallId) {
            return { ...chunk, status: toolExecution.status, output: toolExecution.output };
          }
          return chunk;
        }),
      })),
    })),
  tokenUsage: { input: 0, output: 0, total: 0 },
  updateTokenUsage: (usage: TokenUsage) =>
    set({ tokenUsage: { input: usage.input, output: usage.output, total: usage.total } }),
  busy: false,
  setBusy: (busy: boolean) => set({ busy }),
  blink: true,
  toggleBlink: () => set((state) => ({ blink: !state.blink })),
  tick: 0,
  advanceTick: () => set((state) => ({ tick: (state.tick + 1) % 1_000_000 })),
  terminalCols: process.stdout.columns ?? 80,
  terminalRows: process.stdout.rows ?? 24,
  clearRequested: false,
}));
