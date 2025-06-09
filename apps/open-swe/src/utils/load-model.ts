import { initChatModel } from "langchain/chat_models/universal";
import { GraphConfig } from "@open-swe/shared/open-swe/types";

export enum Task {
  PLANNER = "planner",
  PLANNER_CONTEXT = "plannerContext",
  ACTION_GENERATOR = "actionGenerator",
  PROGRESS_PLAN_CHECKER = "progressPlanChecker",
  SUMMARIZER = "summarizer",
}

const TASK_TO_CONFIG_DEFAULTS_MAP = {
  [Task.PLANNER]: {
    modelName: "anthropic:claude-sonnet-4-0",
    temperature: 0,
  },
  [Task.PLANNER_CONTEXT]: {
    modelName: "anthropic:claude-sonnet-4-0",
    temperature: 0,
  },
  [Task.ACTION_GENERATOR]: {
    modelName: "anthropic:claude-sonnet-4-0",
    temperature: 0,
  },
  [Task.PROGRESS_PLAN_CHECKER]: {
    modelName: "anthropic:claude-sonnet-4-0",
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
  const maxTokens = thinkingBudgetTokens * 4;

  const model = await initChatModel(modelName, {
    modelProvider,
    temperature: thinkingModel ? undefined : temperature,
    ...(thinkingModel && modelProvider === "anthropic"
      ? {
          thinking: { budget_tokens: thinkingBudgetTokens, type: "enabled" },
          maxTokens,
        }
      : { maxTokens: config.configurable?.maxTokens ?? 10_000 }),
  });

  return model;
}
