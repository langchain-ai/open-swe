export enum LLMTask {
  /**
   * Used for programmer tasks. This includes: writing code,
   * generating plans, taking context gathering actions, etc.
   */
  PLANNER = "planner",
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
   * Used for reviewer tasks. This includes: reviewing code,
   * generating plans, taking context gathering actions, etc.
   */
  REVIEWER = "reviewer",
  /**
   * Used for summarizing tasks. This includes: summarizing
   * the conversation history, summarizing actions taken during
   * a task execution, etc. Should be a slightly advanced model.
   */
  SUMMARIZER = "summarizer",
}

export const TASK_TO_CONFIG_DEFAULTS_MAP = {
  [LLMTask.PLANNER]: {
    modelName: "anthropic:claude-sonnet-4-0",
    temperature: 0,
  },
  [LLMTask.PROGRAMMER]: {
    modelName: "anthropic:claude-sonnet-4-0",
    temperature: 0,
  },
  [LLMTask.REVIEWER]: {
    modelName: "anthropic:claude-sonnet-4-0",
    temperature: 0,
  },
  [LLMTask.ROUTER]: {
    modelName: "anthropic:claude-3-5-haiku-latest",
    temperature: 0,
  },
  [LLMTask.SUMMARIZER]: {
    modelName: "anthropic:claude-3-5-haiku-latest",
    temperature: 0,
  },
};
