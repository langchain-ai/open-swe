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
    .custom<PlanItem[]>()
    .default(() => [])
    .langgraph.reducer<PlanItem[]>((_state, update) => update),
  planChangeRequest: z
    .string()
    .nullable()
    .default(() => null)
    .langgraph.reducer((_state, update) => update),
  /**
   * The session ID of the Sandbox to use.
   */
  sandboxSessionId: z
    .string()
    .optional()
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
   * The URL of the repository to clone.
   */
  target_repository: z
    .object({
      owner: z.string(),
      repo: z.string(),
      branch: z.string().optional(),
    })
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "json",
        default: `{
  "owner": "",
  "repo": "",
  "branch": ""
}`,
      },
    }),
  /**
   * The language of the sandbox to use.
   */
  sandbox_language: z
    .enum(["js", "python"])
    .optional()
    .langgraph.metadata({
      x_oap_ui_config: {
        type: "select",
        default: "js",
        description: "The primary language of the sandbox to use.",
        options: [
          {
            label: "JavaScript/TypeScript",
            value: "js",
          },
          {
            label: "Python",
            value: "python",
          },
        ],
      },
    }),
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
});

export type GraphConfig = LangGraphRunnableConfig<
  z.infer<typeof GraphConfiguration> & {
    thread_id: string;
    assistant_id: string;
  }
>;
