import "@langchain/langgraph/zod";
import { z } from "zod";
import {
  addMessages,
  LangGraphRunnableConfig,
  Messages,
} from "@langchain/langgraph/web";
import { BaseMessage } from "@langchain/core/messages";
import { MODEL_OPTIONS, MODEL_OPTIONS_NO_THINKING } from "./models.js";
import { ConfigurableFieldUIMetadata } from "../configurable-metadata.js";

export type PlanItem = {
  /**
   * The index of the plan item. This is the order in which
   * it should be executed.
   */
  index: number;
  /**
   * The actual task to perform.
   */
  plan: string;
  /**
   * Whether or not the plan item has been completed.
   */
  completed: boolean;
  /**
   * A summary of the completed task.
   */
  summary?: string;
};

export type PlanRevision = {
  /**
   * The revision index of the plan.
   * This is used to track edits made to the plan by the agent or user
   */
  revisionIndex: number;
  /**
   * The plans for this task & revision.
   */
  plans: PlanItem[];
  /**
   * Timestamp when this revision was created
   */
  createdAt: number;
  /**
   * Who created this revision (agent or user)
   */
  createdBy: "agent" | "user";
};

export type Task = {
  /**
   * Unique identifier for the task
   */
  id: string;
  /**
   * The index of the user's task in chronological order
   */
  taskIndex: number;
  /**
   * The original user request that created this task
   */
  request: string;
  /**
   * When the task was created
   */
  createdAt: number;
  /**
   * Whether the task is completed
   */
  completed: boolean;
  /**
   * When the task was completed (if applicable)
   */
  completedAt?: number;
  /**
   * Overall summary of the completed task
   */
  summary?: string;
  /**
   * The plans generated for this task.
   * Ordered by revisionIndex, with the latest revision being the active one
   */
  planRevisions: PlanRevision[];
  /**
   * Index of the currently active plan revision
   */
  activeRevisionIndex: number;
  /**
   * Optional parent task id if this task was derived from another task
   */
  parentTaskId?: string;
};

export type TaskPlan = {
  /**
   * All tasks in the system
   */
  tasks: Task[];
  /**
   * Index of the currently active task
   */
  activeTaskIndex: number;
};

export type TargetRepository = {
  owner: string;
  repo: string;
  branch?: string;
};

export const GraphAnnotation = z.object({
  messages: z
    .custom<BaseMessage[]>()
    .default(() => [])
    .langgraph.reducer<Messages>((state, update) => addMessages(state, update)),
  proposedPlan: z
    .array(z.string())
    .default(() => [])
    .langgraph.reducer((_state, update) => update),
  plan: z
    .custom<TaskPlan>()
    .langgraph.reducer<TaskPlan>((_state, update) => update),
  planChangeRequest: z
    .string()
    .nullable()
    .default(() => null)
    .langgraph.reducer((_state, update) => update),
  planContextSummary: z
    .string()
    .default(() => "")
    .langgraph.reducer((_state, update) => update),
  /**
   * The session ID of the Sandbox to use.
   */
  sandboxSessionId: z
    .string()
    .optional()
    .langgraph.reducer((_state, update) => update),
  /**
   * The name of the branch changes in this thread will be pushed to
   */
  branchName: z
    .string()
    .optional()
    .langgraph.reducer((_state, update) => update),
  /**
   * The target repository information
   */
  targetRepository: z
    .custom<TargetRepository>()
    .langgraph.reducer((_state, update) => update),
  codebaseTree: z
    .string()
    .default(() => "")
    .langgraph.reducer((_state, update) => update),
});

export type GraphState = z.infer<typeof GraphAnnotation>;
export type GraphUpdate = Partial<GraphState>;

export const GraphConfigurationMetadata: {
  [key: string]: {
    x_open_swe_ui_config:
      | Omit<ConfigurableFieldUIMetadata, "label">
      | { type: "hidden" };
  };
} = {
  plannerModelName: {
    x_open_swe_ui_config: {
      type: "select",
      default: "anthropic:claude-sonnet-4-0",
      description: "The model to use for planning",
      options: MODEL_OPTIONS_NO_THINKING,
    },
  },
  plannerTemperature: {
    x_open_swe_ui_config: {
      type: "slider",
      default: 0,
      min: 0,
      max: 2,
      step: 0.1,
      description: "Controls randomness (0 = deterministic, 2 = creative)",
    },
  },
  plannerContextModelName: {
    x_open_swe_ui_config: {
      type: "select",
      default: "anthropic:claude-sonnet-4-0",
      description: "The model to use for planning",
      options: MODEL_OPTIONS,
    },
  },
  plannerContextTemperature: {
    x_open_swe_ui_config: {
      type: "slider",
      default: 0,
      min: 0,
      max: 2,
      step: 0.1,
      description: "Controls randomness (0 = deterministic, 2 = creative)",
    },
  },
  actionGeneratorModelName: {
    x_open_swe_ui_config: {
      type: "select",
      default: "anthropic:claude-sonnet-4-0",
      description: "The model to use for action generation",
      options: MODEL_OPTIONS,
    },
  },
  actionGeneratorTemperature: {
    x_open_swe_ui_config: {
      type: "slider",
      default: 0,
      min: 0,
      max: 2,
      step: 0.1,
      description: "Controls randomness (0 = deterministic, 2 = creative)",
    },
  },
  progressPlanCheckerModelName: {
    x_open_swe_ui_config: {
      type: "select",
      default: "anthropic:claude-sonnet-4-0",
      description: "The model to use for progress plan checking",
      options: MODEL_OPTIONS_NO_THINKING,
    },
  },
  progressPlanCheckerTemperature: {
    x_open_swe_ui_config: {
      type: "slider",
      default: 0,
      min: 0,
      max: 2,
      step: 0.1,
      description: "Controls randomness (0 = deterministic, 2 = creative)",
    },
  },
  summarizerModelName: {
    x_open_swe_ui_config: {
      type: "select",
      default: "anthropic:claude-sonnet-4-0",
      description: "The model to use for summarizing the conversation history",
      options: MODEL_OPTIONS_NO_THINKING,
    },
  },
  summarizerTemperature: {
    x_open_swe_ui_config: {
      type: "slider",
      default: 0,
      min: 0,
      max: 2,
      step: 0.1,
      description: "Controls randomness (0 = deterministic, 2 = creative)",
    },
  },
  maxContextActions: {
    x_open_swe_ui_config: {
      type: "number",
      default: 6,
      min: 1,
      max: 20,
      description:
        "Maximum number of context gathering actions during planning",
    },
  },
  maxTokens: {
    x_open_swe_ui_config: {
      type: "number",
      default: 10_000,
      min: 1,
      max: 64_000,
      description:
        "The maximum number of tokens to generate in an individual generation",
    },
  },
  "x-github-installation-token": {
    x_open_swe_ui_config: {
      type: "hidden",
    },
  },
  "x-github-access-token": {
    x_open_swe_ui_config: {
      type: "hidden",
    },
  },
};

export const GraphConfiguration = z.object({
  /**
   * The model ID to use for the planning step.
   * This includes initial planning, and rewriting.
   * @default "anthropic:claude-sonnet-4-0"
   */
  plannerModelName: z
    .string()
    .optional()
    .langgraph.metadata(GraphConfigurationMetadata.plannerModelName),
  /**
   * The temperature to use for the planning step.
   * This includes initial planning, and rewriting.
   * If selecting a reasoning model, this will be ignored.
   * @default 0
   */
  plannerTemperature: z
    .number()
    .optional()
    .langgraph.metadata(GraphConfigurationMetadata.plannerTemperature),
  /**
   * The model ID to use for the planning step.
   * This includes initial planning, and rewriting.
   * @default "anthropic:claude-sonnet-4-0"
   */
  plannerContextModelName: z
    .string()
    .optional()
    .langgraph.metadata(GraphConfigurationMetadata.plannerContextModelName),
  /**
   * The temperature to use for the planning step.
   * This includes initial planning, and rewriting.
   * If selecting a reasoning model, this will be ignored.
   * @default 0
   */
  plannerContextTemperature: z
    .number()
    .optional()
    .langgraph.metadata(GraphConfigurationMetadata.plannerContextTemperature),

  /**
   * The model ID to use for action generation.
   * @default "anthropic:claude-sonnet-4-0"
   */
  actionGeneratorModelName: z
    .string()
    .optional()
    .langgraph.metadata(GraphConfigurationMetadata.actionGeneratorModelName),
  /**
   * The temperature to use for action generation.
   * If selecting a reasoning model, this will be ignored.
   * @default 0
   */
  actionGeneratorTemperature: z
    .number()
    .optional()
    .langgraph.metadata(GraphConfigurationMetadata.actionGeneratorTemperature),

  /**
   * The model ID to use for progress plan checking.
   * @default "anthropic:claude-sonnet-4-0"
   */
  progressPlanCheckerModelName: z
    .string()
    .optional()
    .langgraph.metadata(
      GraphConfigurationMetadata.progressPlanCheckerModelName,
    ),
  /**
   * The temperature to use for progress plan checking.
   * If selecting a reasoning model, this will be ignored.
   * @default 0
   */
  progressPlanCheckerTemperature: z
    .number()
    .optional()
    .langgraph.metadata(
      GraphConfigurationMetadata.progressPlanCheckerTemperature,
    ),

  /**
   * The model ID to use for summarizing the conversation history.
   * @default "anthropic:claude-sonnet-4-0"
   */
  summarizerModelName: z
    .string()
    .optional()
    .langgraph.metadata(GraphConfigurationMetadata.summarizerModelName),
  /**
   * The temperature to use for summarizing the conversation history.
   * If selecting a reasoning model, this will be ignored.
   * @default 0
   */
  summarizerTemperature: z
    .number()
    .optional()
    .langgraph.metadata(GraphConfigurationMetadata.summarizerTemperature),

  /**
   * The maximum number of context gathering actions to take during planning.
   * Each action consists of 2 messages (request & result), plus 1 human message.
   * Total messages = maxContextActions * 2 + 1
   * @default 6
   */
  maxContextActions: z
    .number()
    .optional()
    .langgraph.metadata(GraphConfigurationMetadata.maxContextActions),
  /**
   * The maximum number of tokens to generate in an individual generation.
   * @default 10_000
   */
  maxTokens: z
    .number()
    .optional()
    .default(() => 10_000)
    .langgraph.metadata(GraphConfigurationMetadata.maxTokens),
  /**
   * The user's GitHub installation token. To be used to take actions on behalf of the user.
   */
  "x-github-installation-token": z
    .string()
    .optional()
    .langgraph.metadata(
      GraphConfigurationMetadata["x-github-installation-token"],
    ),
  /**
   * The user's GitHub access token. To be used in requests to get information about the user.
   */
  "x-github-access-token": z
    .string()
    .optional()
    .langgraph.metadata(GraphConfigurationMetadata["x-github-access-token"]),
});

export type GraphConfig = LangGraphRunnableConfig<
  z.infer<typeof GraphConfiguration> & {
    thread_id: string;
    assistant_id: string;
  }
>;
