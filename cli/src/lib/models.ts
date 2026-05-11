import type { ModelOption, Provider } from '@types';

export const modelOptions: ModelOption[] = [
  { id: 0, label: 'claude-opus-4.7-low', name: 'claude-opus-4-7', provider: 'anthropic', effort: 'low', contextWindow: 1000000 },
  { id: 1, label: 'claude-opus-4.7-medium', name: 'claude-opus-4-7', provider: 'anthropic', effort: 'medium', contextWindow: 1000000 },
  { id: 2, label: 'claude-opus-4.7-high', name: 'claude-opus-4-7', provider: 'anthropic', effort: 'high', contextWindow: 1000000 },
  { id: 3, label: 'claude-opus-4.7-xhigh', name: 'claude-opus-4-7', provider: 'anthropic', effort: 'xhigh', contextWindow: 1000000 },
  { id: 4, label: 'gpt-5.5-low', name: 'gpt-5.5', provider: 'openai', effort: 'low', contextWindow: 400000 },
  { id: 5, label: 'gpt-5.5-medium', name: 'gpt-5.5', provider: 'openai', effort: 'medium', contextWindow: 400000 },
  { id: 6, label: 'gpt-5.5-high', name: 'gpt-5.5', provider: 'openai', effort: 'high', contextWindow: 400000 },
  { id: 7, label: 'gpt-5.5-xhigh', name: 'gpt-5.5', provider: 'openai', effort: 'xhigh', contextWindow: 400000 },
  { id: 8, label: 'gemini-3-pro', name: 'gemini-3-pro-preview', provider: 'google', effort: 'medium', contextWindow: 1048576 },
  { id: 9, label: 'gemini-3-flash', name: 'gemini-3-flash-preview', provider: 'google', effort: 'medium', contextWindow: 1048576 },
];

export function getProviderForModel(modelName: string): Provider | null {
  const model = modelOptions.find(m => m.name === modelName || m.label === modelName);
  return model?.provider ?? null;
}

export function isKnownModelConfig(name: string, effort: string): boolean {
  return modelOptions.some(m => m.name === name && m.effort === effort);
}
