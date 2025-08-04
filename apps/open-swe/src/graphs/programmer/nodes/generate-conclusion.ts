import {
  GraphConfig,
  GraphState,
  GraphUpdate,
  PlanItem,
} from "@open-swe/shared/open-swe/types";
import { loadModel } from "../../../utils/llms/index.js";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import { getMessageContentString } from "@open-swe/shared/messages";
import { getMessageString } from "../../../utils/message/content.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { formatUserRequestPrompt } from "../../../utils/user-request.js";
import {
  completeTask,
  getActivePlanItems,
  getActiveTask,
} from "@open-swe/shared/open-swe/tasks";
import { addTaskPlanToIssue } from "../../../utils/github/issue-task.js";
import { trackCachePerformance } from "../../../utils/caching.js";
import { getModelManager } from "../../../utils/llms/model-manager.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import { Command, END } from "@langchain/langgraph";

const logger = createLogger(LogLevel.INFO, "GenerateConclusionNode");

const prompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

You have just completed all of the tasks in the plan:
{COMPLETED_TASKS}

Since you've successfully completed the user's request, you should now generate a short, concise concision. It can be helpful here to outline all of the changes you've made to the codebase, any additional steps you think the user should take, any relevant informatioon from the conversation hostiry below, etc.
Your concision message should be concise and to the point, you do NOT want to include any details which are not ABSOLUTELY NECESSARY.
`;

const formatPrompt = (taskPlan: PlanItem[]): string => {
  return prompt.replace(
    "{COMPLETED_TASKS}",
    taskPlan.map((p) => `${p.index}. ${p.plan}`).join("\n"),
  );
};

export async function generateConclusion(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const model = await loadModel(config, LLMTask.SUMMARIZER);
  const modelManager = getModelManager();
  const modelName = modelManager.getModelNameForTask(
    config,
    LLMTask.SUMMARIZER,
  );

  const userRequestPrompt = formatUserRequestPrompt(state.messages);
  const userMessage = `${userRequestPrompt}

The full conversation history is as follows:
${state.internalMessages.map(getMessageString).join("\n")}

Given all of this, please respond with the concise conclusion. Do not include any additional text besides the conclusion.`;

  logger.info("Generating conclusion");

  const response = await model.invoke([
    {
      role: "system",
      content: formatPrompt(getActivePlanItems(state.taskPlan)),
    },
    {
      role: "user",
      content: userMessage,
    },
  ]);

  logger.info("✅ Successfully generated conclusion.");
  const activeTaskId = getActiveTask(state.taskPlan).id;
  const updatedTaskPlan = completeTask(
    state.taskPlan,
    activeTaskId,
    getMessageContentString(response.content),
  );

  // Update the github issue to include the new overall task summary (only if not in local mode)
  if (!isLocalMode(config) && state.githubIssueId) {
    await addTaskPlanToIssue(
      {
        githubIssueId: state.githubIssueId,
        targetRepository: state.targetRepository,
      },
      config,
      updatedTaskPlan,
    );
  }

  const graphUpdate: GraphUpdate = {
    messages: [response],
    internalMessages: [response],
    taskPlan: updatedTaskPlan,
    tokenData: trackCachePerformance(response, modelName),
  };

  // Route based on mode: END for local mode, open-pr for sandbox mode
  if (isLocalMode(config)) {
    logger.info("Local mode: routing to END");
    return new Command({
      update: graphUpdate,
      goto: END,
    });
  } else {
    logger.info("Sandbox mode: routing to open-pr");
    return new Command({
      update: graphUpdate,
      goto: "open-pr",
    });
  }
}
