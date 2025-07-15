import { initChatModel } from "langchain/chat_models/universal";
import { GraphConfig } from "@open-swe/shared/open-swe/types";

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

  const model = await initChatModel(modelName, {
    modelProvider,
    temperature: thinkingModel ? undefined : temperature,
    ...(thinkingModel && modelProvider === "anthropic"
      ? {
          thinking: { budget_tokens: thinkingBudgetTokens, type: "enabled" },
          maxTokens: thinkingMaxTokens,
        }
      : { maxTokens }),
  });

  return model;
}
