import { interrupt } from "@langchain/langgraph";
import { sessionPlanTool } from "../tools/index.js";
import { GraphState, GraphConfig, GraphUpdate } from "../types.js";
import { loadModel } from "../utils/load-model.js";
import { HumanInterrupt, HumanResponse } from "@langchain/langgraph/prebuilt";
import { z } from "zod";

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

In this step, you are expected to generate a high-level plan to address the user's request. The plan should be a list of actions to take, in order, to address the user's request. You should not include any code in the plan, only a list of actions to take.

You MUST adhere to the following criteria when generating the plan:
- You do not have access to the codebase yet, so you cannot inspect it or make assumptions about it.
- Your plan should be high-level in nature, but should still be specific enough to be actionable.
- If you can not generate a plan due to a lack of context, you are permitted to ask the user followup questions.
  - If asking followup questions, ensure every question is asked in a single message to avoid back and forth.
  - Your questions should be concise and to the point. Remember that you are not including code or technical details in your plan, so your questions should be focused on high-level issues.
- When you are ready to generate the plan, ensure you call the 'session_plan' tool.
`;

export async function generatePlan(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const model = await loadModel(config);
  const modelWithTools = model.bindTools([sessionPlanTool], {
    tool_choice: "auto",
  });

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: systemPrompt,
    },
    ...state.messages,
  ]);

  if (response.tool_calls?.length) {
    return {
      proposedPlan: response.tool_calls[0].args.plan,
      plan: [],
    };
  }

  // No tool calls generated, instead we should just return the messages.
  return {
    messages: response,
    proposedPlan: [],
    plan: [],
  };
}
