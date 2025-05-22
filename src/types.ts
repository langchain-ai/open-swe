import "@langchain/langgraph/zod";
import { z } from "zod";
import {
  Annotation,
  LangGraphRunnableConfig,
  MessagesAnnotation,
} from "@langchain/langgraph";

type PlanItem = {
  id: string;
  plan: string;
  completed: boolean;
};

export type TargetRepository = {
  owner: string;
  repo: string;
  branch?: string;
};

export const GraphAnnotation = Annotation.Root({
  messages: MessagesAnnotation.spec.messages,
  plan: Annotation<PlanItem[]>({
    reducer: (_state, update) => update,
    default: () => [],
  }),
});

export type GraphState = typeof GraphAnnotation.State;
export type GraphUpdate = typeof GraphAnnotation.Update;

export const MCPConfig = z.object({
  /**
   * The MCP server URL.
   */
  url: z.string(),
  /**
   * The list of tools to provide to the LLM.
   */
  tools: z.array(z.string()),
});

export const GraphConfiguration = z.object({
  /**
   * The session ID of the Sandbox to use.
   */
  sandbox_session_id: z
    .string()
    .optional()
    .langgraph.metadata({
      x_lg_ui_config: {
        type: "hidden",
      },
    }),
  /**
   * The URL of the repository to clone.
   */
  target_repository: z
    .object({
      owner: z.string(),
      repo: z.string(),
      branch: z.string().optional(),
    })
    .langgraph.metadata({}),
  /**
   * The language of the sandbox to use.
   */
  sandbox_language: z.enum(["js", "python"]).optional().langgraph.metadata({}),
});

export type GraphConfig = LangGraphRunnableConfig<
  z.infer<typeof GraphConfiguration> & {
    thread_id: string;
    assistant_id: string;
  }
>;
