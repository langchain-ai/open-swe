import { initChatModel } from "langchain/chat_models/universal";
import { GraphConfig } from "../types.js";

export async function loadModel(config: GraphConfig) {
  const modelStr =
    config.configurable?.modelName ?? "anthropic:claude-3-7-sonnet-latest";
  const [modelProvider, ...modelNameParts] = modelStr.split(":");
  const modelName = modelNameParts.join(":");
  const model = await initChatModel(modelName, {
    modelProvider,
    temperature: config.configurable?.temperature ?? 0,
    maxTokens: config.configurable?.maxTokens ?? undefined,
  });

  return model;
}
