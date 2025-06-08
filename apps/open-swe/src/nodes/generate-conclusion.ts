import { GraphConfig, GraphState, GraphUpdate, PlanItem } from "../types.js";
import { loadModel, Task } from "../utils/load-model.js";
import {
  getMessageContentString,
  getMessageString,
} from "../utils/message/content.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { getUserRequest } from "../utils/user-request.js";
import {
  completeTask,
  getActivePlanItems,
  getActiveTask,
} from "../utils/task-plan.js";

const logger = createLogger(LogLevel.INFO, "GenerateConclusionNode");

const prompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

You have just completed all of the tasks in the plan:
{COMPLETED_TASKS}

Since you've successfully completed the user's request, you should now generate a short, concise concision. It can be helpful here to outline all of the changes you've made to the codebase, any additional steps you think the user should take, any relevant informatioon from the conversation hostiry below, etc.
Your concision message should be concise and to the point, you do NOT want to include any details which are not ABSOLUTELY NECESSARY.
`;

const formatPrompt = (plan: PlanItem[]): string => {
  return prompt.replace(
    "{COMPLETED_TASKS}",
    plan.map((p) => `${p.index}. ${p.plan}`).join("\n"),
  );
};

export async function generateConclusion(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const model = await loadModel(config, Task.SUMMARIZER);

  const userRequest = getUserRequest(state.messages);
  const userMessage = `The user's initial request is as follows:
${userRequest || "No user message found"}

The conversation history is as follows:
${state.messages.map(getMessageString).join("\n")}

Given all of this, please respond with the concise conclusion. Do not include any additional text besides the conclusion.`;

  logger.info("Generating conclusion");

  const response = await model.invoke([
    {
      role: "system",
      content: formatPrompt(getActivePlanItems(state.plan)),
    },
    {
      role: "user",
      content: userMessage,
    },
  ]);

  logger.info("✅ Successfully generated conclusion. Ending run. 👋");
  const activeTaskId = getActiveTask(state.plan).id;
  const updatedTaskPlan = completeTask(
    state.plan,
    activeTaskId,
    getMessageContentString(response.content),
  );

  return {
    messages: [response],
    plan: updatedTaskPlan,
  };
}
