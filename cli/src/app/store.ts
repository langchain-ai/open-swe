import { create } from 'zustand';
import type { ModelConfig, Message, TokenUsage, ToolExecution, ApiKeys } from '@types';
import { randomUUID } from 'crypto';

// The Welcome banner UI replaces the textual greeting. We still seed an initial
// invisible "session start" message so messages.length >= 1 invariants used by
// the App for splitting static/live items hold from frame zero. The renderer
// treats empty system text chunks as no-ops.
const createWelcomeMessage = (): Message => ({
  id: randomUUID(),
  author: 'system',
  chunks: [{ kind: 'text', text: '' }],
});

type Store = {
  apiKeys: ApiKeys;
  setApiKeys: (keys: ApiKeys) => void;
  setApiKey: (provider: keyof ApiKeys, key: string) => void;
  clearApiKey: (provider: keyof ApiKeys) => void;
  clearApiKeys: () => void;
  modelConfig: ModelConfig;
  setModelConfig: (config: ModelConfig) => void;
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
  resetRequested: boolean;
  clearRequested: boolean;
};

export const useStore = create<Store>((set, get) => ({
  apiKeys: {},
  setApiKeys: (keys: ApiKeys) => set({ apiKeys: keys }),
  setApiKey: (provider: keyof ApiKeys, key: string) => set((state) => ({
    apiKeys: { ...state.apiKeys, [provider]: key }
  })),
  clearApiKey: (provider: keyof ApiKeys) => set((state) => {
    const next = { ...state.apiKeys };
    delete next[provider];
    return { apiKeys: next };
  }),
  clearApiKeys: () => set({ apiKeys: {} }),
  modelConfig: { name: 'claude-opus-4-7', provider: 'anthropic', effort: 'high' },
  setModelConfig: (config: ModelConfig) => set({ modelConfig: config }),
  messages: [createWelcomeMessage()],
  addMessage: (msg: Omit<Message, 'id'>) => set((state) => ({ messages: [...state.messages, { ...msg, id: randomUUID() }] })),
  resetMessages: () => set({ messages: [createWelcomeMessage()], tokenUsage: { input: 0, output: 0, total: 0 } }),
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
    set({
      tokenUsage: {
        input: usage.input,
        output: usage.output,
        total: usage.total,
      },
    }),
  busy: false,
  setBusy: (busy: boolean) => set({ busy }),
  blink: true,
  toggleBlink: () => set((state) => ({ blink: !state.blink })),
  tick: 0,
  advanceTick: () => set((state) => ({ tick: (state.tick + 1) % 1_000_000 })),
  terminalCols: process.stdout.columns ?? 80,
  terminalRows: process.stdout.rows ?? 24,
  resetRequested: false,
  clearRequested: false,
}));
