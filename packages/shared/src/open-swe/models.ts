import { GraphConfig } from "./types.js";
import { isLocalMode } from "./local-mode.js";

export const MODEL_OPTIONS = [
  // TODO: Test these then re-enable
  // {
  //   label: "Claude Sonnet 4 (Extended Thinking)",
  //   value: "anthropic:extended-thinking:claude-sonnet-4-0",
  // },
  // {
  //   label: "Claude Opus 4 (Extended Thinking)",
  //   value: "anthropic:extended-thinking:claude-opus-4-0",
  // },
  {
    label: "Claude Sonnet 4",
    value: "anthropic:claude-sonnet-4-0",
  },
  {
    label: "Claude Opus 4.1",
    value: "anthropic:claude-opus-4-1",
  },
  {
    label: "Claude Opus 4",
    value: "anthropic:claude-opus-4-0",
  },
  {
    label: "Claude 3.7 Sonnet",
    value: "anthropic:claude-3-7-sonnet-latest",
  },
  {
    label: "Claude 3.5 Sonnet",
    value: "anthropic:claude-3-5-sonnet-latest",
  },
  {
    label: "Claude 3.5 Haiku",
    value: "anthropic:claude-3-5-haiku-latest",
  },
  {
    label: "GPT 5",
    value: "openai:gpt-5",
  },
  {
    label: "GPT 5 mini",
    value: "openai:gpt-5-mini",
  },
  {
    label: "GPT 5 nano",
    value: "openai:gpt-5-nano",
  },
  {
    label: "o4",
    value: "openai:o4",
  },
  {
    label: "o4 mini",
    value: "openai:o4-mini",
  },
  {
    label: "o3",
    value: "openai:o3",
  },
  {
    label: "o3 mini",
    value: "openai:o3-mini",
  },
  {
    label: "GPT 4o",
    value: "openai:gpt-4o",
  },
  {
    label: "GPT 4o mini",
    value: "openai:gpt-4o-mini",
  },
  {
    label: "GPT 4.1",
    value: "openai:gpt-4.1",
  },
  {
    label: "GPT 4.1 mini",
    value: "openai:gpt-4.1-mini",
  },
  {
    label: "Gemini 2.5 Pro",
    value: "google-genai:gemini-2.5-pro",
  },
  {
    label: "Gemini 2.5 Flash",
    value: "google-genai:gemini-2.5-flash",
  },
];

export const OLLAMA_MODELS = [
  {
    label: "Qwen2.5 Coder 7B",
    value: "ollama:qwen2.5-coder:7b",
  },
  {
    label: "Qwen2.5 Coder 14B",
    value: "ollama:qwen2.5-coder:14b",
  },
  {
    label: "Qwen2.5 Coder 32B",
    value: "ollama:qwen2.5-coder:32b",
  },
  {
    label: "GPT-OSS 20B",
    value: "ollama:gpt-oss:20b",
  },
  {
    label: "GPT-OSS 120B",
    value: "ollama:gpt-oss:120b",
  },
  {
    label: "DeepSeek R1 8B",
    value: "ollama:deepseek-r1:8b",
  },
  {
    label: "DeepSeek R1 14B",
    value: "ollama:deepseek-r1:14b",
  },
  {
    label: "DeepSeek R1 32B",
    value: "ollama:deepseek-r1:32b",
  },
  {
    label: "DeepSeek R1 70B",
    value: "ollama:deepseek-r1:70b",
  },
];

export const MODEL_OPTIONS_NO_THINKING = MODEL_OPTIONS.filter(
  ({ value }) =>
    !value.includes("extended-thinking") || !value.startsWith("openai:o"),
);

/**
 * Get available models based on configuration
 * Returns MODEL_OPTIONS plus OLLAMA_MODELS when in local mode, otherwise just MODEL_OPTIONS
 */
export function getAvailableModels(config?: GraphConfig) {
  const baseModels = MODEL_OPTIONS;

  if (isLocalMode(config)) {
    return [...baseModels, ...OLLAMA_MODELS];
  }

  return baseModels;
}

/**
 * Get available models (no thinking) based on configuration
 * Returns filtered models plus OLLAMA_MODELS when in local mode, otherwise just filtered models
 */
export function getAvailableModelsNoThinking(config?: GraphConfig) {
  const baseModels = MODEL_OPTIONS_NO_THINKING;

  if (isLocalMode(config)) {
    // Filter out thinking models from Ollama models as well
    const ollamaModelsNoThinking = OLLAMA_MODELS.filter(
      ({ value }) =>
        !value.includes("extended-thinking") && !value.startsWith("ollama:o"),
    );
    return [...baseModels, ...ollamaModelsNoThinking];
  }

  return baseModels;
}
