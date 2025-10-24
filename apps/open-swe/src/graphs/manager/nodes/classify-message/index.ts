import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import { createLangGraphClient } from "../../../../utils/langgraph-client.js";
import {
  BaseMessage,
  HumanMessage,
  isHumanMessage,
  RemoveMessage,
} from "@langchain/core/messages";
import { z } from "zod";
import {
  loadModel,
  supportsParallelToolCallsParam,
} from "../../../../utils/llms/index.js";
import { LLMTask } from "@openswe/shared/open-swe/llm-task";
import { Command, END } from "@langchain/langgraph";
import { getMessageContentString } from "@openswe/shared/messages";
import { getIssueService } from "../../../../services/issue-service.js";
import { createIssueFieldsFromMessages } from "../../utils/generate-issue-fields.js";
import {
  extractContentWithoutDetailsFromIssueBody,
  extractIssueTitleAndContentFromMessage,
  formatContentForIssueBody,
} from "../../../../utils/issue-messages.js";
import { BASE_CLASSIFICATION_SCHEMA } from "./schemas.js";
import { HumanResponse } from "@langchain/langgraph/prebuilt";
import {
  LOCAL_MODE_HEADER,
  OPEN_SWE_STREAM_MODE,
  PLANNER_GRAPH_ID,
} from "@openswe/shared/constants";
import { createLogger, LogLevel } from "../../../../utils/logger.js";
import { createClassificationPromptAndToolSchema } from "./utils.js";
import { StreamMode, Thread } from "@langchain/langgraph-sdk";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";
import { PlannerGraphState } from "@openswe/shared/open-swe/planner/types";
import { GraphState } from "@openswe/shared/open-swe/types";
import { shouldCreateIssue } from "../../../../utils/should-create-issue.js";
const logger = createLogger(LogLevel.INFO, "ClassifyMessage");

/**
 * Classify the latest human message to determine how to route the request.
 * Requests can be routed to:
 * 1. reply - dont need to plan, just reply. This could be if the user sends a message which is not classified as a request, or if the programmer/planner is already running.
 *   a. if the planner/programmer is already running, we'll simply reply with
 */
export async function classifyMessage(
  state: ManagerGraphState,
  config: GraphConfig,
): Promise<Command> {
  const userMessage = state.messages.findLast(isHumanMessage);
  if (!userMessage) {
    throw new Error("No human message found.");
  }

  const langGraphClient = createLangGraphClient({
    defaultHeaders: {
      ...(isLocalMode(config) ? { [LOCAL_MODE_HEADER]: "true" } : {}),
    },
  });

  const plannerThread: Thread<PlannerGraphState> | undefined =
    state.plannerSession?.threadId
      ? await langGraphClient.threads.get(state.plannerSession.threadId)
      : undefined;
  const plannerThreadValues = plannerThread?.values;
  const programmerThread: Thread<GraphState> | undefined =
    plannerThreadValues?.programmerSession?.threadId
      ? await langGraphClient.threads.get(
          plannerThreadValues.programmerSession.threadId,
        )
      : undefined;

  const programmerStatus = programmerThread?.status ?? "not_started";
  const plannerStatus = plannerThread?.status ?? "not_started";

  // If the issueId is defined, fetch the most recent task plan (if exists). Otherwise fallback to state task plan
  const taskPlan = state.taskPlan;

  const { prompt, schema } = createClassificationPromptAndToolSchema({
    programmerStatus,
    plannerStatus,
    messages: state.messages,
    taskPlan,
    proposedPlan: undefined,
    requestSource: userMessage.additional_kwargs?.requestSource as
      | string
      | undefined,
  });
  const respondAndRouteTool = {
    name: "respond_and_route",
    description: "Respond to the user's message and determine how to route it.",
    schema,
  };
  const model = await loadModel(config, LLMTask.ROUTER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    LLMTask.ROUTER,
  );
  const modelWithTools = model.bindTools([respondAndRouteTool], {
    tool_choice: respondAndRouteTool.name,
    ...(modelSupportsParallelToolCallsParam
      ? {
          parallel_tool_calls: false,
        }
      : {}),
  });

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: prompt,
    },
    {
      role: "user",
      content: extractContentWithoutDetailsFromIssueBody(
        getMessageContentString(userMessage.content),
      ),
    },
  ]);

  const toolCall = response.tool_calls?.[0];
  if (!toolCall) {
    throw new Error("No tool call found.");
  }
  const toolCallArgs = toolCall.args as z.infer<
    typeof BASE_CLASSIFICATION_SCHEMA
  >;

  if (toolCallArgs.route === "no_op") {
    // If it's a no_op, just add the message to the state and return.
    const commandUpdate: ManagerGraphUpdate = {
      messages: [response],
      workspacePath: state.workspacePath,
    };
    return new Command({
      update: commandUpdate,
      goto: END,
    });
  }

  if ((toolCallArgs.route as string) === "create_new_issue") {
    // Route to node which kicks off new manager run, passing in the full conversation history.
    const commandUpdate: ManagerGraphUpdate = {
      messages: [response],
      workspacePath: state.workspacePath,
    };
    return new Command({
      update: commandUpdate,
      goto: "create-new-session",
    });
  }

  const shouldCreateIssues = shouldCreateIssue(config);
  let issueId = state.issueId;

  const newMessages: BaseMessage[] = [response];

  if (shouldCreateIssues) {
    const issueService = getIssueService(config);

    // If it's not a no_op, ensure there is an issue with the user's request.
    if (!issueId) {
      const { title } = await createIssueFieldsFromMessages(
        state.messages,
        config.configurable,
      );
      const { content: body } = extractIssueTitleAndContentFromMessage(
        getMessageContentString(userMessage.content),
      );

      const newIssue = await issueService.createIssue({
        repo: state.targetRepository,
        title,
        body: formatContentForIssueBody(body),
      });
      if (!newIssue) {
        throw new Error("Failed to create issue.");
      }
      issueId = Number(newIssue.id);
      // Ensure we remove the old message, and replace it with an exact copy,
      // but with the issue ID & isOriginalIssue set in additional_kwargs.
      newMessages.push(
        ...[
          new RemoveMessage({
            id: userMessage.id ?? "",
          }),
          new HumanMessage({
            ...userMessage,
            additional_kwargs: {
              issueId: issueId,
              isOriginalIssue: true,
            },
          }),
        ],
      );
    } else if (issueId && state.messages.filter(isHumanMessage).length > 1) {
      // If there already is an issue ID in state and multiple human messages, add any
      // human messages to the issue which weren't already added.
      const messagesNotInIssue = state.messages
        .filter(isHumanMessage)
        .filter((message) => {
          // If the message doesn't contain `issueId` in additional kwargs, it hasn't been added to the issue.
          return !message.additional_kwargs?.issueId;
        });

      const createCommentsPromise = messagesNotInIssue.map(async (message) => {
        const createdIssue = await issueService.createComment({
          repo: state.targetRepository,
          issueId: issueId!,
          body: getMessageContentString(message.content),
        });
        if (!createdIssue?.id) {
          throw new Error("Failed to create issue comment");
        }
        newMessages.push(
          ...[
            new RemoveMessage({
              id: message.id ?? "",
            }),
            new HumanMessage({
              ...message,
              additional_kwargs: {
                issueId,
                issueCommentId: createdIssue.id,
                ...((toolCallArgs.route as string) ===
                "start_planner_for_followup"
                  ? {
                      isFollowup: true,
                    }
                  : {}),
              },
            }),
          ],
        );
      });

      await Promise.all(createCommentsPromise);

      let newPlannerId: string | undefined;
      let goto = END;

      if (plannerStatus === "interrupted") {
        if (!state.plannerSession?.threadId) {
          throw new Error("No planner session found. Unable to resume planner.");
        }
        // We need to resume the planner session via a 'response' so that it can re-plan
        const plannerResume: HumanResponse = {
          type: "response",
          args: "resume planner",
        };
        logger.info("Resuming planner session");
        const newPlannerRun = await langGraphClient.runs.create(
          state.plannerSession?.threadId,
          PLANNER_GRAPH_ID,
          {
            command: {
              resume: plannerResume,
            },
            config: {
              configurable: {
                ...(state.workspacePath
                  ? { workspacePath: state.workspacePath }
                  : {}),
              },
            },
            streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
          },
        );
        newPlannerId = newPlannerRun.run_id;
        logger.info("Planner session resumed", {
          runId: newPlannerRun.run_id,
          threadId: state.plannerSession.threadId,
        });
      }

      if (toolCallArgs.route === "start_planner_for_followup") {
        goto = "start-planner";
      }

      // After creating the new comment, we can add the message to state and end.
      const commandUpdate: ManagerGraphUpdate = {
        messages: newMessages,
        workspacePath: state.workspacePath,
        ...(newPlannerId && state.plannerSession?.threadId
          ? {
              plannerSession: {
                threadId: state.plannerSession.threadId,
                runId: newPlannerId,
              },
            }
          : {}),
      };
      return new Command({
        update: commandUpdate,
        goto,
      });
    }
  }

  // Issue handling (creation/commenting) is complete if enabled.

  const commandUpdate: ManagerGraphUpdate = {
    messages: newMessages,
    ...(issueId ? { issueId } : {}),
    workspacePath: state.workspacePath,
  };

  if (
    (toolCallArgs.route as any) === "update_programmer" ||
    (toolCallArgs.route as any) === "update_planner" ||
    (toolCallArgs.route as any) === "resume_and_update_planner"
  ) {
    // If the route is one of the above, we don't need to do anything since the issue now contains
    // the new messages, and the coding agent will handle pulling them in. This should never be
    // reachable since we should return early after adding the issue comment, but include anyways...
    return new Command({
      update: commandUpdate,
      goto: END,
    });
  }

  if (
    toolCallArgs.route === "start_planner" ||
    toolCallArgs.route === "start_planner_for_followup"
  ) {
    // Always kickoff a new start planner node. This will enqueue new runs on the planner graph.
    return new Command({
      update: commandUpdate,
      goto: "start-planner",
    });
  }

  throw new Error(`Invalid route: ${toolCallArgs.route}`);
}
