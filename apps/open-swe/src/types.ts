import "@langchain/langgraph/zod";
import { z } from "zod";
import {
  addMessages,
  LangGraphRunnableConfig,
  Messages,
} from "@langchain/langgraph";
import { BaseMessage } from "@langchain/core/messages";

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
  codebaseContext: z
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
});

export type GraphState = z.infer<typeof GraphAnnotation>;
export type GraphUpdate = Partial<GraphState>;

const MODEL_OPTIONS = [
  {
    label: "Claude Sonnet 4 (Extended Thinking)",
    value: "anthropic:extended-thinking:claude-sonnet-4-0",
  },
  {
    label: "Claude Opus 4 (Extended Thinking)",
    value: "anthropic:extended-thinking:claude-opus-4-0",
  },
  {
    label: "Claude Sonnet 4",
    value: "anthropic:claude-sonnet-4-0",
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
    label: "GPT 4.1",
    value: "openai:gpt-4.1",
  },
  {
    label: "Gemini 2.5 Pro Preview",
    value: "google-genai:gemini-2.5-pro-preview-05-06",
  },
  {
    label: "Gemini 2.5 Flash Preview",
    value: "google-genai:gemini-2.5-flash-preview-05-20",
  },
];

const MODEL_OPTIONS_NO_THINKING = MODEL_OPTIONS.filter(
  ({ value }) =>
    !value.includes("extended-thinking") || !value.startsWith("openai:o"),
);

export const GraphConfiguration = z.object({
  /**
   * The model ID to use for the planning step.
   * This includes initial planning, and rewriting.
   * @default "anthropic:claude-sonnet-4-0"
   */
  plannerModelName: z
    .string()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "select",
        default: "anthropic:claude-sonnet-4-0",
        description: "The model to use for planning",
        // Do not show extended thinking models
        options: MODEL_OPTIONS_NO_THINKING,
      },
    }),
  /**
   * The temperature to use for the planning step.
   * This includes initial planning, and rewriting.
   * If selecting a reasoning model, this will be ignored.
   * @default 0
   */
  plannerTemperature: z
    .number()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "slider",
        default: 0,
        min: 0,
        max: 2,
        step: 0.1,
        description: "Controls randomness (0 = deterministic, 2 = creative)",
      },
    }),
  /**
   * The model ID to use for the planning step.
   * This includes initial planning, and rewriting.
   * @default "anthropic:claude-sonnet-4-0"
   */
  plannerContextModelName: z
    .string()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "select",
        default: "anthropic:claude-sonnet-4-0",
        description: "The model to use for planning",
        options: MODEL_OPTIONS,
      },
    }),
  /**
   * The temperature to use for the planning step.
   * This includes initial planning, and rewriting.
   * If selecting a reasoning model, this will be ignored.
   * @default 0
   */
  plannerContextTemperature: z
    .number()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "slider",
        default: 0,
        min: 0,
        max: 2,
        step: 0.1,
        description: "Controls randomness (0 = deterministic, 2 = creative)",
      },
    }),

  /**
   * The model ID to use for action generation.
   * @default "anthropic:claude-sonnet-4-0"
   */
  actionGeneratorModelName: z
    .string()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "select",
        default: "anthropic:claude-sonnet-4-0",
        description: "The model to use for action generation",
        options: MODEL_OPTIONS,
      },
    }),
  /**
   * The temperature to use for action generation.
   * If selecting a reasoning model, this will be ignored.
   * @default 0
   */
  actionGeneratorTemperature: z
    .number()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "slider",
        default: 0,
        min: 0,
        max: 2,
        step: 0.1,
        description: "Controls randomness (0 = deterministic, 2 = creative)",
      },
    }),

  /**
   * The model ID to use for progress plan checking.
   * @default "anthropic:claude-sonnet-4-0"
   */
  progressPlanCheckerModelName: z
    .string()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "select",
        default: "anthropic:claude-sonnet-4-0",
        description: "The model to use for progress plan checking",
        options: MODEL_OPTIONS_NO_THINKING,
      },
    }),
  /**
   * The temperature to use for progress plan checking.
   * If selecting a reasoning model, this will be ignored.
   * @default 0
   */
  progressPlanCheckerTemperature: z
    .number()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "slider",
        default: 0,
        min: 0,
        max: 2,
        step: 0.1,
        description: "Controls randomness (0 = deterministic, 2 = creative)",
      },
    }),

  /**
   * The model ID to use for summarizing the conversation history.
   * @default "anthropic:claude-sonnet-4-0"
   */
  summarizerModelName: z
    .string()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "select",
        default: "anthropic:claude-sonnet-4-0",
        description:
          "The model to use for summarizing the conversation history",
        options: MODEL_OPTIONS_NO_THINKING,
      },
    }),
  /**
   * The temperature to use for summarizing the conversation history.
   * If selecting a reasoning model, this will be ignored.
   * @default 0
   */
  summarizerTemperature: z
    .number()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "slider",
        default: 0,
        min: 0,
        max: 2,
        step: 0.1,
        description: "Controls randomness (0 = deterministic, 2 = creative)",
      },
    }),

  /**
   * The maximum number of context gathering actions to take during planning.
   * Each action consists of 2 messages (request & result), plus 1 human message.
   * Total messages = maxContextActions * 2 + 1
   * @default 6
   */
  maxContextActions: z
    .number()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "number",
        default: 6,
        min: 1,
        max: 20,
        description:
          "Maximum number of context gathering actions during planning",
      },
    }),
  /**
   * The maximum number of tokens to generate in an individual generation.
   * @default 10_000
   */
  maxTokens: z
    .number()
    .optional()
    .default(() => 10_000)
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "number",
        default: 10_000,
        min: 1,
        max: 64_000,
        description:
          "The maximum number of tokens to generate in an individual generation",
      },
    }),
  /**
   * The user's GitHub installation token. To be used to take actions on behalf of the user.
   */
  "x-github-installation-token": z
    .string()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "hidden",
      },
    }),
  /**
   * The user's GitHub access token. To be used in requests to get information about the user.
   */
  "x-github-access-token": z
    .string()
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "hidden",
      },
    }),
});

export type GraphConfig = LangGraphRunnableConfig<
  z.infer<typeof GraphConfiguration> & {
    thread_id: string;
    assistant_id: string;
  }
>;
