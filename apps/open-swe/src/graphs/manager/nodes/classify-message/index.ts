import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@open-swe/shared/open-swe/manager/types";
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
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import { Command, END } from "@langchain/langgraph";
import { getMessageContentString } from "@open-swe/shared/messages";
import {
  createIssue,
  createIssueComment,
} from "../../../../utils/github/api.js";
import { getGitHubTokensFromConfig } from "../../../../utils/github-tokens.js";
import { createIssueFieldsFromMessages } from "../../utils/generate-issue-fields.js";
import {
  extractContentWithoutDetailsFromIssueBody,
  extractIssueTitleAndContentFromMessage,
  formatContentForIssueBody,
} from "../../../../utils/github/issue-messages.js";
import { getDefaultHeaders } from "../../../../utils/default-headers.js";
import { BASE_CLASSIFICATION_SCHEMA } from "./schemas.js";
import { getPlansFromIssue } from "../../../../utils/github/issue-task.js";
import { HumanResponse } from "@langchain/langgraph/prebuilt";
import {
  OPEN_SWE_STREAM_MODE,
  PLANNER_GRAPH_ID,
} from "@open-swe/shared/constants";
import { createLogger, LogLevel } from "../../../../utils/logger.js";
import { createClassificationPromptAndToolSchema } from "./utils.js";
import { RequestSource } from "../../../../constants.js";
import { StreamMode, Thread } from "@langchain/langgraph-sdk";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { Client } from "@langchain/langgraph-sdk";
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

  let plannerThread: Thread<PlannerGraphState> | undefined;
  let programmerThread: Thread<GraphState> | undefined;
  let langGraphClient: Client | undefined;

  if (!isLocalMode(config)) {
    // Only create LangGraph client if not in local mode
    langGraphClient = createLangGraphClient({
      defaultHeaders: getDefaultHeaders(config),
    });

    plannerThread = state.plannerSession?.threadId
      ? await langGraphClient.threads.get(state.plannerSession.threadId)
      : undefined;
    const plannerThreadValues = plannerThread?.values;
    programmerThread = plannerThreadValues?.programmerSession?.threadId
      ? await langGraphClient.threads.get(
          plannerThreadValues.programmerSession.threadId,
        )
      : undefined;
  }

  const programmerStatus = programmerThread?.status ?? "not_started";
  const plannerStatus = plannerThread?.status ?? "not_started";

  // If the githubIssueId is defined, fetch the most recent task plan (if exists). Otherwise fallback to state task plan
  const issuePlans = state.githubIssueId
    ? await getPlansFromIssue(state, config)
    : null;
  const taskPlan = issuePlans?.taskPlan ?? state.taskPlan;

  const { prompt, schema } = createClassificationPromptAndToolSchema({
    programmerStatus,
    plannerStatus,
    messages: state.messages,
    taskPlan,
    proposedPlan: issuePlans?.proposedPlan ?? undefined,
    requestSource: userMessage.additional_kwargs?.requestSource as
      | RequestSource
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
    };
    return new Command({
      update: commandUpdate,
      goto: "create-new-session",
    });
  }

  if (isLocalMode(config)) {
    // In local mode, just route to planner without GitHub issue creation
    const newMessages: BaseMessage[] = [response];
    const commandUpdate: ManagerGraphUpdate = {
      messages: newMessages,
    };

    if (
      toolCallArgs.route === "start_planner" ||
      toolCallArgs.route === "start_planner_for_followup"
    ) {
      return new Command({
        update: commandUpdate,
        goto: "start-planner",
      });
    }

    throw new Error(
      `Unsupported route for local mode received: ${toolCallArgs.route}`,
    );
  }

  const { githubAccessToken } = getGitHubTokensFromConfig(config);
  let githubIssueId = state.githubIssueId;

  const newMessages: BaseMessage[] = [response];

  // If it's not a no_op, ensure there is a GitHub issue with the user's request.
  if (!githubIssueId) {
    const { title } = await createIssueFieldsFromMessages(
      state.messages,
      config.configurable,
    );
    const { content: body } = extractIssueTitleAndContentFromMessage(
      getMessageContentString(userMessage.content),
    );

    const newIssue = await createIssue({
      owner: state.targetRepository.owner,
      repo: state.targetRepository.repo,
      title,
      body: formatContentForIssueBody(body),
      githubAccessToken,
    });
    if (!newIssue) {
      throw new Error("Failed to create issue.");
    }
    githubIssueId = newIssue.number;
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
            githubIssueId: githubIssueId,
            isOriginalIssue: true,
          },
        }),
      ],
    );
  } else if (
    githubIssueId &&
    state.messages.filter(isHumanMessage).length > 1
  ) {
    // If there already is a GitHub issue ID in state, and multiple human messages, add any
    // human messages to the issue which weren't already added.
    const messagesNotInIssue = state.messages
      .filter(isHumanMessage)
      .filter((message) => {
        // If the message doesn't contain `githubIssueId` in additional kwargs, it hasn't been added to the issue.
        return !message.additional_kwargs?.githubIssueId;
      });

    const createCommentsPromise = messagesNotInIssue.map(async (message) => {
      const createdIssue = await createIssueComment({
        owner: state.targetRepository.owner,
        repo: state.targetRepository.repo,
        issueNumber: githubIssueId,
        body: getMessageContentString(message.content),
        githubToken: githubAccessToken,
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
              githubIssueId,
              githubIssueCommentId: createdIssue.id,
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
      if (!langGraphClient) {
        throw new Error("LangGraph client not initialized");
      }
      const newPlannerRun = await langGraphClient.runs.create(
        state.plannerSession?.threadId,
        PLANNER_GRAPH_ID,
        {
          command: {
            resume: plannerResume,
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

  // Issue has been created, and any missing human messages have been added to it.

  const commandUpdate: ManagerGraphUpdate = {
    messages: newMessages,
    ...(githubIssueId ? { githubIssueId } : {}),
  };

  if (
    (toolCallArgs.route as any) === "update_programmer" ||
    (toolCallArgs.route as any) === "update_planner" ||
    (toolCallArgs.route as any) === "resume_and_update_planner"
  ) {
    // If the route is one of the above, we don't need to do anything since the issue now contains
    // the new messages, and the coding agent will handle pulling them in. This should never be
    // reachable since we should return early after adding the Github comment, but include anyways...
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
