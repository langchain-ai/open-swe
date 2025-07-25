import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { getModelManager } from "./model-manager.js";
import { FallbackRunnable } from "./runtime-fallback.js";

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
    modelName: "openai:gpt-4.1-mini",
    temperature: 0,
  },
};

export async function loadModel(config: GraphConfig, task: Task) {
  const modelManager = getModelManager();

  const model = await modelManager.loadModel(config, task);
  if (!model) {
    throw new Error(`Model loading returned undefined for task: ${task}`);
  }
  const fallbackModel = new FallbackRunnable(model, config, task, modelManager);
  return fallbackModel;
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
