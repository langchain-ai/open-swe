import { InteractionPhase, TaskPlan } from "@openswe/shared/open-swe/types";
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
import { getActivePlanItems } from "@openswe/shared/open-swe/tasks";
import {
  getHumanMessageString,
  getToolMessageString,
  getUnknownMessageString,
} from "../../../../utils/message/content.js";
import { getMessageContentString } from "@openswe/shared/messages";
import { ThreadStatus } from "@langchain/langgraph-sdk";
import {
  CLASSIFICATION_SYSTEM_PROMPT,
  CONVERSATION_HISTORY_PROMPT,
  CREATE_NEW_ISSUE_ROUTING_OPTION,
  UPDATE_PLANNER_ROUTING_OPTION,
  UPDATE_PROGRAMMER_ROUTING_OPTION,
  PROPOSED_PLAN_PROMPT,
  RESUME_AND_UPDATE_PLANNER_ROUTING_OPTION,
  TASK_PLAN_PROMPT,
  FEATURE_GRAPH_ROUTING_OPTION,
} from "./prompts.js";
import { createClassificationSchema } from "./schemas.js";

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
  requestSource?: string;
  phase?: InteractionPhase;
}): {
  prompt: string;
  schema: z.ZodTypeAny;
} {
  const phase = inputs.phase ?? "design";
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

  const showCreateIssueOption =
    inputs.programmerStatus !== "not_started" ||
    inputs.plannerStatus !== "not_started";

  const designRoutes = ["feature_graph_orchestrator", "no_op"] as const;
  const plannerRoutes = [
    "feature_graph_orchestrator",
    "update_planner",
    "resume_and_update_planner",
    "create_new_issue",
    "no_op",
  ] as const;
  const programmerRoutes = [
    "feature_graph_orchestrator",
    "update_programmer",
    "create_new_issue",
    "no_op",
  ] as const;

  let allowedRoutes: string[];
  let phaseInstruction: string;

  switch (phase) {
    case "planner": {
      allowedRoutes = [
        plannerRoutes[0],
        ...(plannerRunning ? [plannerRoutes[1]] : []),
        ...(plannerInterrupted ? [plannerRoutes[2]] : []),
        ...(showCreateIssueOption ? [plannerRoutes[3]] : []),
        plannerRoutes[4],
      ];
      phaseInstruction =
        "Planner phase: prioritize planning updates or resuming planning threads; keep feature-graph alignment when the request shifts.";
      break;
    }
    case "programmer": {
      allowedRoutes = [
        programmerRoutes[0],
        ...(programmerRunning ? [programmerRoutes[1]] : []),
        ...(showCreateIssueOption ? [programmerRoutes[2]] : []),
        programmerRoutes[3],
      ];
      phaseInstruction =
        "Programmer phase: focus on coding updates and ensure routing only supplements the active implementation work.";
      break;
    }
    default: {
      allowedRoutes = [...designRoutes];
      phaseInstruction =
        "Design phase: stay in feature-discovery mode, refining the feature graph before initiating planning or coding.";
      break;
    }
  }

  const prompt = CLASSIFICATION_SYSTEM_PROMPT.replaceAll(
    "{PROGRAMMER_STATUS}",
    THREAD_STATUS_READABLE_STRING_MAP[inputs.programmerStatus],
  )
    .replaceAll(
      "{PLANNER_STATUS}",
      THREAD_STATUS_READABLE_STRING_MAP[inputs.plannerStatus],
    )
    .replaceAll("{ROUTING_OPTIONS}", allowedRoutes.join(", "))
    .replaceAll(
      "{FEATURE_GRAPH_ROUTING_OPTION}",
      FEATURE_GRAPH_ROUTING_OPTION,
    )
    .replaceAll(
      "{UPDATE_PROGRAMMER_ROUTING_OPTION}",
      programmerRunning ? UPDATE_PROGRAMMER_ROUTING_OPTION : "",
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

  const promptWithPhaseInstruction = prompt.replace(
    "# Assistant Statuses",
    `# Phase\n${phaseInstruction}\n\n# Assistant Statuses`,
  );

  const schema = createClassificationSchema(
    allowedRoutes as [string, ...string[]],
  );

  return {
    prompt: promptWithPhaseInstruction,
    schema,
  };
}
