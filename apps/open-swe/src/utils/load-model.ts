import { initChatModel } from "langchain/chat_models/universal";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { isAllowedUser } from "./github/allowed-users.js";
import { decryptSecret } from "@open-swe/shared/crypto";

export enum Task {
  /**
   * Used for programmer tasks. This includes: writing code,
   * generating plans, taking context gathering actions, etc.
   */
  PROGRAMMER = "programmer",
  /**
   * Used for routing tasks. This includes: initial request
   * routing to different agents.
   */
  ROUTER = "router",
  /**
   * Used for summarizing tasks. This includes: summarizing
   * the conversation history, summarizing actions taken during
   * a task execution, etc. Should be a slightly advanced model.
   */
  SUMMARIZER = "summarizer",
}

const TASK_TO_CONFIG_DEFAULTS_MAP = {
  [Task.PROGRAMMER]: {
    modelName: "anthropic:claude-sonnet-4-0",
    temperature: 0,
  },
  [Task.ROUTER]: {
    modelName: "anthropic:claude-3-5-haiku-latest",
    temperature: 0,
  },
  [Task.SUMMARIZER]: {
    modelName: "anthropic:claude-sonnet-4-0",
    temperature: 0,
  },
};

const providerToApiKey = (
  providerName: string,
  apiKeys: Record<string, string>,
): string => {
  switch (providerName) {
    case "openai":
      return apiKeys.openaiApiKey;
    case "anthropic":
      return apiKeys.anthropicApiKey;
    case "google-genai":
      return apiKeys.googleApiKey;
    default:
      throw new Error(`Unknown provider: ${providerName}`);
  }
};

export async function loadModel(config: GraphConfig, task: Task) {
  const modelStr =
    config.configurable?.[`${task}ModelName`] ??
    TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName;
  const temperature =
    config.configurable?.[`${task}Temperature`] ??
    TASK_TO_CONFIG_DEFAULTS_MAP[task].temperature;

  const [modelProvider, ...modelNameParts] = modelStr.split(":");

  let thinkingModel = false;
  if (modelNameParts[0] === "extended-thinking") {
    // Using a thinking model. Remove it from the model name.
    modelNameParts.shift();
    thinkingModel = true;
  }

  const modelName = modelNameParts.join(":");
  if (modelProvider === "openai" && modelName.startsWith("o")) {
    thinkingModel = true;
  }

  const thinkingBudgetTokens = 5000;
  const thinkingMaxTokens = thinkingBudgetTokens * 4;

  let maxTokens = config.configurable?.maxTokens ?? 10_000;
  if (modelName.includes("claude-3-5-haiku")) {
    // The max tokens for haiku is 8192
    maxTokens = maxTokens > 8_192 ? 8_192 : maxTokens;
  }

  // TODO: Fix types
  const userLogin = (config.configurable as any)?.langgraph_auth_user
    ?.display_name;
  const secretsEncryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
  if (!secretsEncryptionKey) {
    throw new Error("SECRETS_ENCRYPTION_KEY environment variable is required");
  }
  if (!userLogin) {
    throw new Error("User login not found in config");
  }
  const apiKeys = config.configurable?.apiKeys;
  let apiKey: string | null = null;
  if (!isAllowedUser(userLogin)) {
    if (!apiKeys) {
      throw new Error("API keys not found in config");
    }
    apiKey = decryptSecret(
      providerToApiKey(modelProvider, apiKeys),
      secretsEncryptionKey,
    );
    if (!apiKey) {
      throw new Error("No API key found for provider: " + modelProvider);
    }
  }

  const model = await initChatModel(modelName, {
    modelProvider,
    temperature: thinkingModel ? undefined : temperature,
    ...(apiKey ? { apiKey } : {}),
    ...(thinkingModel && modelProvider === "anthropic"
      ? {
          thinking: { budget_tokens: thinkingBudgetTokens, type: "enabled" },
          maxTokens: thinkingMaxTokens,
        }
      : { maxTokens }),
  });

  return model;
}

const MODELS_NO_PARALLEL_TOOL_CALLING = ["openai:o3", "openai:o3-mini"];

export function supportsParallelToolCallsParam(
  config: GraphConfig,
  task: Task,
): boolean {
  const modelStr =
    config.configurable?.[`${task}ModelName`] ??
    TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName;

  return !MODELS_NO_PARALLEL_TOOL_CALLING.some((model) => modelStr === model);
}
