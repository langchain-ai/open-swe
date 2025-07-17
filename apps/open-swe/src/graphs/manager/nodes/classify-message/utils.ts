import { TaskPlan } from "@open-swe/shared/open-swe/types";
import {
  AIMessage,
  BaseMessage,
  isAIMessage,
  isHumanMessage,
  isToolMessage,
  ToolMessage,
} from "@langchain/core/messages";
import { z } from "zod";
import { removeLastHumanMessage } from "../../../../utils/message/modify-array.js";
import { formatPlanPrompt } from "../../../../utils/plan-prompt.js";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import {
  getHumanMessageString,
  getToolMessageString,
  getUnknownMessageString,
} from "../../../../utils/message/content.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import { ThreadStatus } from "@langchain/langgraph-sdk";
import {
  CLASSIFICATION_SYSTEM_PROMPT,
  CONVERSATION_HISTORY_PROMPT,
  CREATE_NEW_ISSUE_ROUTING_OPTION,
  UPDATE_PLANNER_ROUTING_OPTION,
  UPDATE_PROGRAMMER_ROUTING_OPTION,
  PROPOSED_PLAN_PROMPT,
  RESUME_AND_UPDATE_PLANNER_ROUTING_OPTION,
  START_PLANNER_ROUTING_OPTION,
  TASK_PLAN_PROMPT,
  START_PLANNER_FOR_FOLLOWUP_ROUTING_OPTION,
} from "./prompts.js";
import { createClassificationSchema } from "./schemas.js";
import { RequestSource } from "../../../../constants.js";

const THREAD_STATUS_READABLE_STRING_MAP = {
  not_started: "not started",
  busy: "currently running",
  idle: "not running",
  interrupted: "interrupted -- awaiting human response",
  error: "error",
};

function formatMessageForClassification(message: BaseMessage): string {
  if (isHumanMessage(message)) {
    return getHumanMessageString(message);
  }

  // Special formatting for the AI messages as we don't want to show what status was called since the available statuses are dynamic.
  if (isAIMessage(message)) {
    const aiMessage = message as AIMessage;
    const toolCallName = aiMessage.tool_calls?.[0]?.name;
    const toolCallResponseStr = aiMessage.tool_calls?.[0]?.args?.response;
    const toolCallStr =
      toolCallName && toolCallResponseStr
        ? `Tool call: ${toolCallName}\nArgs: ${JSON.stringify({ response: toolCallResponseStr }, null)}\n`
        : "";
    const content = getMessageContentString(aiMessage.content);
    return `<assistant message-id=${aiMessage.id ?? "No ID"}>\nContent: ${content}\n${toolCallStr}</assistant>`;
  }

  if (isToolMessage(message)) {
    const toolMessage = message as ToolMessage;
    return getToolMessageString(toolMessage);
  }

  return getUnknownMessageString(message);
}

export function createClassificationPromptAndToolSchema(inputs: {
  programmerStatus: ThreadStatus | "not_started";
  plannerStatus: ThreadStatus | "not_started";
  messages: BaseMessage[];
  taskPlan: TaskPlan;
  proposedPlan?: string[];
  requestSource?: RequestSource;
}): {
  prompt: string;
  schema: z.ZodTypeAny;
} {
  const conversationHistoryWithoutLatest = removeLastHumanMessage(
    inputs.messages,
  );
  const formattedTaskPlanPrompt = inputs.taskPlan
    ? TASK_PLAN_PROMPT.replaceAll(
        "{TASK_PLAN}",
        formatPlanPrompt(getActivePlanItems(inputs.taskPlan)),
      )
    : null;
  const formattedProposedPlanPrompt = inputs.proposedPlan?.length
    ? PROPOSED_PLAN_PROMPT.replace(
        "{PROPOSED_PLAN}",
        inputs.proposedPlan
          .map((p, index) => `  ${index + 1}: ${p}`)
          .join("\n"),
      )
    : null;

  const formattedConversationHistoryPrompt =
    conversationHistoryWithoutLatest?.length
      ? CONVERSATION_HISTORY_PROMPT.replaceAll(
          "{CONVERSATION_HISTORY}",
          conversationHistoryWithoutLatest
            .map(formatMessageForClassification)
            .join("\n"),
        )
      : null;

  const programmerRunning = inputs.programmerStatus === "busy";
  const plannerRunning = inputs.plannerStatus === "busy";
  const plannerInterrupted = inputs.plannerStatus === "interrupted";
  const plannerNotStarted = inputs.plannerStatus === "not_started";
  // If both are idle, we should allow 'start_planner' to start a new planning run on the same request.
  const plannerAndProgrammerIdle =
    inputs.programmerStatus === "idle" && inputs.plannerStatus === "idle";

  const showCreateIssueOption =
    inputs.programmerStatus !== "not_started" ||
    inputs.plannerStatus !== "not_started";

  const routingOptions = [
    ...(programmerRunning ? ["update_programmer"] : []),
    ...(plannerNotStarted ? ["start_planner"] : []),
    ...(plannerAndProgrammerIdle ? ["start_planner_for_followup"] : []),
    ...(plannerRunning ? ["update_planner"] : []),
    ...(plannerInterrupted ? ["resume_and_update_planner"] : []),
    ...(showCreateIssueOption ? ["create_new_issue"] : []),
    "no_op",
  ];

  const prompt = CLASSIFICATION_SYSTEM_PROMPT.replaceAll(
    "{PROGRAMMER_STATUS}",
    THREAD_STATUS_READABLE_STRING_MAP[inputs.programmerStatus],
  )
    .replaceAll(
      "{PLANNER_STATUS}",
      THREAD_STATUS_READABLE_STRING_MAP[inputs.plannerStatus],
    )
    .replaceAll("{ROUTING_OPTIONS}", routingOptions.join(", "))
    .replaceAll(
      "{UPDATE_PROGRAMMER_ROUTING_OPTION}",
      programmerRunning ? UPDATE_PROGRAMMER_ROUTING_OPTION : "",
    )
    .replaceAll(
      "{START_PLANNER_ROUTING_OPTION}",
      plannerNotStarted ? START_PLANNER_ROUTING_OPTION : "",
    )
    .replaceAll(
      "{START_PLANNER_FOR_FOLLOWUP_ROUTING_OPTION}",
      plannerAndProgrammerIdle ? START_PLANNER_FOR_FOLLOWUP_ROUTING_OPTION : "",
    )
    .replaceAll(
      "{UPDATE_PLANNER_ROUTING_OPTION}",
      plannerRunning ? UPDATE_PLANNER_ROUTING_OPTION : "",
    )
    .replaceAll(
      "{RESUME_AND_UPDATE_PLANNER_ROUTING_OPTION}",
      plannerInterrupted ? RESUME_AND_UPDATE_PLANNER_ROUTING_OPTION : "",
    )
    .replaceAll(
      "{CREATE_NEW_ISSUE_ROUTING_OPTION}",
      showCreateIssueOption ? CREATE_NEW_ISSUE_ROUTING_OPTION : "",
    )
    .replaceAll(
      "{TASK_PLAN_PROMPT}",
      formattedTaskPlanPrompt ?? formattedProposedPlanPrompt ?? "",
    )
    .replaceAll(
      "{CONVERSATION_HISTORY_PROMPT}",
      formattedConversationHistoryPrompt ?? "",
    )
    .replaceAll(
      "{REQUEST_SOURCE}",
      inputs.requestSource ?? "no source provided",
    );

  const schema = createClassificationSchema(
    routingOptions as [string, ...string[]],
  );

  return {
    prompt,
    schema,
  };
}
