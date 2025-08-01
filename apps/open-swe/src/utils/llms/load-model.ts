import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { getModelManager, Provider } from "./model-manager.js";
import { FallbackRunnable } from "../runtime-fallback.js";
import { Task, TASK_TO_CONFIG_DEFAULTS_MAP } from "./constants.js";
import { BindToolsInput } from "@langchain/core/language_models/chat_models";
import { BaseMessageLike } from "@langchain/core/messages";

export async function loadModel(
  config: GraphConfig,
  task: Task,
  options?: {
    providerTools?: Record<Provider, BindToolsInput[]>;
    providerMessages?: Record<Provider, BaseMessageLike[]>;
  },
) {
  const modelManager = getModelManager();

  const model = await modelManager.loadModel(config, task);
  if (!model) {
    throw new Error(`Model loading returned undefined for task: ${task}`);
  }
  const fallbackModel = new FallbackRunnable(
    model,
    config,
    task,
    modelManager,
    options,
  );
  return fallbackModel;
}

export const MODELS_NO_PARALLEL_TOOL_CALLING = ["openai:o3", "openai:o3-mini"];

export function supportsParallelToolCallsParam(
  config: GraphConfig,
  task: Task,
): boolean {
  const modelStr =
    config.configurable?.[`${task}ModelName`] ??
    TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName;

  return !MODELS_NO_PARALLEL_TOOL_CALLING.some((model) => modelStr === model);
}
