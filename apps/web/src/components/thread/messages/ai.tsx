import { parsePartialJson } from "@langchain/core/output_parsers";
import {
  AIMessage,
  Message,
  StreamMode,
  ToolMessage,
} from "@langchain/langgraph-sdk";
import { getContentString } from "../utils";
import { MarkdownText } from "../markdown-text";
import {
  LoadExternalComponent,
  UIMessage,
} from "@langchain/langgraph-sdk/react-ui";
import { ToolCalls, ToolResult } from "./tool-calls";
import { MessageContentComplex } from "@langchain/core/messages";
import { Fragment } from "react/jsx-runtime";
import { useQueryState, parseAsBoolean } from "nuqs";
import { Interrupt } from "./interrupt";
import { ActionStep, ActionItemProps } from "@/components/gen-ui/action-step";
import { TaskSummary } from "@/components/gen-ui/task-summary";
import { PullRequestOpened } from "@/components/gen-ui/pull-request-opened";
import {
  MarkTaskCompleted,
  MarkTaskIncomplete,
} from "@/components/gen-ui/task-review";
import { DiagnoseErrorAction } from "@/components/v2/diagnose-error-action";
import { WriteTechnicalNotes } from "@/components/gen-ui/write-technical-notes";
import { CodeReviewStarted } from "@/components/gen-ui/code-review-started";
import { RequestHumanHelp } from "@/components/gen-ui/request-human-help";
import { ToolCall } from "@langchain/core/messages/tool";
import {
  createApplyPatchToolFields,
  createShellToolFields,
  createMarkTaskCompletedToolFields,
  createMarkTaskNotCompletedToolFields,
  createGrepToolFields,
  createOpenPrToolFields,
  createInstallDependenciesToolFields,
  createCodeReviewMarkTaskCompletedFields,
  createCodeReviewMarkTaskNotCompleteFields,
  createDiagnoseErrorToolFields,
  createGetURLContentToolFields,
  createSearchDocumentForToolFields,
  createWriteTechnicalNotesToolFields,
  createConversationHistorySummaryToolFields,
  createRequestHumanHelpToolFields,
  createReviewStartedToolFields,
  createScratchpadFields,
  createTextEditorToolFields,
  createViewToolFields,
} from "@open-swe/shared/open-swe/tools";
import { z } from "zod";
import { isAIMessageSDK, isToolMessageSDK } from "@/lib/langchain-messages";
import { useStream } from "@langchain/langgraph-sdk/react";
import { ConversationHistorySummary } from "@/components/gen-ui/conversation-summary";
import { getMessageContentString } from "@open-swe/shared/messages";
import { HumanResponse } from "@langchain/langgraph/prebuilt";
import { OPEN_SWE_STREAM_MODE } from "@open-swe/shared/constants";
import { CustomNodeEvent } from "@open-swe/shared/open-swe/custom-node-events";

// Used only for Zod type inference.
const dummyRepo = { owner: "dummy", repo: "dummy" };
const shellTool = createShellToolFields(dummyRepo);
type ShellToolArgs = z.infer<typeof shellTool.schema>;
const applyPatchTool = createApplyPatchToolFields(dummyRepo);
type ApplyPatchToolArgs = z.infer<typeof applyPatchTool.schema>;
const markTaskCompletedTool = createMarkTaskCompletedToolFields();
type MarkTaskCompletedToolArgs = z.infer<typeof markTaskCompletedTool.schema>;
const markTaskNotCompletedTool = createMarkTaskNotCompletedToolFields();
type MarkTaskNotCompletedToolArgs = z.infer<
  typeof markTaskNotCompletedTool.schema
>;
const reviewStartedTool = createReviewStartedToolFields();
type ReviewStartedToolArgs = z.infer<typeof reviewStartedTool.schema>;
const grepTool = createGrepToolFields(dummyRepo);
type GrepToolArgs = z.infer<typeof grepTool.schema>;
const openPrTool = createOpenPrToolFields();
type OpenPrToolArgs = z.infer<typeof openPrTool.schema>;
const installDependenciesTool = createInstallDependenciesToolFields(dummyRepo);
type InstallDependenciesToolArgs = z.infer<
  typeof installDependenciesTool.schema
>;
const scratchpadTool = createScratchpadFields("");
type ScratchpadToolArgs = z.infer<typeof scratchpadTool.schema>;
const markFinalReviewTaskCompletedTool =
  createCodeReviewMarkTaskCompletedFields();
type MarkFinalReviewTaskCompletedToolArgs = z.infer<
  typeof markFinalReviewTaskCompletedTool.schema
>;
const markFinalReviewTaskIncompleteTool =
  createCodeReviewMarkTaskNotCompleteFields();
type MarkFinalReviewTaskIncompleteToolArgs = z.infer<
  typeof markFinalReviewTaskIncompleteTool.schema
>;

const diagnoseErrorTool = createDiagnoseErrorToolFields();
type DiagnoseErrorToolArgs = z.infer<typeof diagnoseErrorTool.schema>;

const getURLContentTool = createGetURLContentToolFields();
type GetURLContentToolArgs = z.infer<typeof getURLContentTool.schema>;
const searchDocumentForTool = createSearchDocumentForToolFields();
type SearchDocumentForToolArgs = z.infer<typeof searchDocumentForTool.schema>;

const writeTechnicalNotesTool = createWriteTechnicalNotesToolFields();
type WriteTechnicalNotesToolArgs = z.infer<
  typeof writeTechnicalNotesTool.schema
>;

const conversationHistorySummaryTool =
  createConversationHistorySummaryToolFields();
type ConversationHistorySummaryToolArgs = z.infer<
  typeof conversationHistorySummaryTool.schema
>;
const requestHumanHelpTool = createRequestHumanHelpToolFields();
type RequestHumanHelpToolArgs = z.infer<typeof requestHumanHelpTool.schema>;
const textEditorTool = createTextEditorToolFields(
  {
    owner: "dummy",
    repo: "dummy",
  },
  {},
);
type TextEditorToolArgs = z.infer<typeof textEditorTool.schema>;

const viewTool = createViewToolFields(dummyRepo);
type ViewToolArgs = z.infer<typeof viewTool.schema>;

// Helper function to detect MCP tools by checking if tool name is NOT in known tools
function isMcpTool(toolName: string): boolean {
  const knownToolNames = [
    shellTool.name,
    applyPatchTool.name,
    installDependenciesTool.name,
    scratchpadTool.name,
    getURLContentTool.name,
    openPrTool.name,
    diagnoseErrorTool.name,
    requestHumanHelpTool.name,
    textEditorTool.name,
    viewTool.name,
  ];
  return !knownToolNames.some((t) => t === toolName);
}

function CustomComponent({
  message,
  thread,
}: {
  message: Message;
  thread: ReturnType<typeof useStream>;
}) {
  const values = thread.values;
  const customComponents =
    "ui" in values
      ? (values.ui as UIMessage[]).filter(
          (ui) => ui.metadata?.message_id === message.id,
        )
      : [];

  if (!customComponents?.length) return null;
  return (
    <Fragment key={message.id}>
      {customComponents.map((customComponent) => (
        <LoadExternalComponent
          key={customComponent.id}
          stream={thread}
          message={customComponent}
          meta={{ ui: customComponent }}
        />
      ))}
    </Fragment>
  );
}

function parseAnthropicStreamedToolCalls(
  content: MessageContentComplex[],
): AIMessage["tool_calls"] {
  const toolCallContents = content.filter((c) => c.type === "tool_use" && c.id);

  return toolCallContents.map((tc) => {
    const toolCall = tc as Record<string, any>;
    let json: Record<string, any> = {};
    if (toolCall?.input) {
      try {
        json = parsePartialJson(toolCall.input) ?? {};
      } catch {
        // Pass
      }
    }
    return {
      name: toolCall.name ?? "",
      id: toolCall.id ?? "",
      args: json,
      type: "tool_call",
    };
  });
}

export function mapToolMessageToActionStepProps(
  message: ToolMessage,
  threadMessages: Message[],
): ActionItemProps {
  const toolCall: ToolCall | undefined = threadMessages
    .filter(isAIMessageSDK)
    .flatMap((m) => m.tool_calls ?? [])
    .find((tc) => tc.id === message.tool_call_id);

  const aiMessage = threadMessages
    .filter(isAIMessageSDK)
    .find((m) => m.tool_calls?.some((tc) => tc.id === message.tool_call_id));
  const reasoningText = aiMessage
    ? getContentString(aiMessage.content)
    : undefined;

  const status: ActionItemProps["status"] = "done";
  const success = message.status !== "error";

  const msgContent = getContentString(message.content);
  const output = msgContent === "" ? "Empty string" : msgContent;

  if (toolCall?.name === shellTool.name) {
    const args = toolCall.args as ShellToolArgs;
    return {
      actionType: shellTool.name as "shell",
      status,
      success,
      command: args.command || [],
      workdir: args.workdir,
      output,
      reasoningText,
    };
  } else if (toolCall?.name === applyPatchTool.name) {
    const args = toolCall.args as ApplyPatchToolArgs;
    return {
      actionType: "apply-patch",
      status,
      success,
      file_path: args.file_path || "",
      diff: args.diff,
      reasoningText,
      errorMessage: !success ? getContentString(message.content) : undefined,
    };
  } else if (toolCall?.name === grepTool.name) {
    const args = toolCall.args as GrepToolArgs;
    return {
      actionType: "grep",
      status,
      success,
      query: args.query || "",
      match_string: args.match_string || false,
      case_sensitive: args.case_sensitive || false,
      context_lines: args.context_lines || 0,
      max_results: args.max_results || 0,
      follow_symlinks: args.follow_symlinks || false,
      exclude_files: args.exclude_files || "",
      include_files: args.include_files || "",
      file_types: args.file_types || [],
      output,
      reasoningText,
    };
  } else if (toolCall?.name === installDependenciesTool.name) {
    const args = toolCall.args as InstallDependenciesToolArgs;
    return {
      actionType: "install_dependencies",
      status,
      success,
      command: args.command || "",
      workdir: args.workdir || "",
      output,
      reasoningText,
    };
  } else if (toolCall?.name === scratchpadTool.name) {
    const args = toolCall.args as ScratchpadToolArgs;
    return {
      actionType: "scratchpad",
      status,
      success,
      scratchpad: args.scratchpad || [],
      reasoningText,
    };
  } else if (toolCall?.name === getURLContentTool.name) {
    const args = toolCall.args as GetURLContentToolArgs;
    return {
      actionType: "get_url_content",
      status,
      success,
      url: args.url || "",
      output,
      reasoningText,
    };
  } else if (toolCall?.name === searchDocumentForTool.name) {
    const args = toolCall.args as SearchDocumentForToolArgs;
    return {
      actionType: "search_document_for",
      status,
      success,
      url: args.url || "",
      query: args.query || "",
      output,
      reasoningText,
    };
  } else if (toolCall?.name === textEditorTool.name) {
    const args = toolCall.args as TextEditorToolArgs;
    return {
      actionType: "text_editor",
      status,
      success,
      command: args.command || "view",
      path: args.path || "",
      view_range: args.view_range,
      old_str: args.old_str,
      new_str: args.new_str,
      file_text: args.file_text,
      insert_line: args.insert_line,
      output,
      reasoningText,
    };
  } else if (toolCall?.name === viewTool.name) {
    const args = toolCall.args as ViewToolArgs;
    return {
      actionType: "text_editor",
      status,
      success,
      command: args.command || "view",
      path: args.path || "",
      view_range: args.view_range as [number, number] | undefined,
      output,
      reasoningText,
    };
  } else if (toolCall && isMcpTool(toolCall.name)) {
    return {
      actionType: "mcp",
      status,
      success,
      toolName: toolCall.name,
      args: toolCall.args as Record<string, any>,
      output,
      reasoningText,
    };
  }
  return {
    status: "loading",
    summaryText: reasoningText,
  };
}

export function AssistantMessage({
  message,
  threadId,
  assistantId,
  forceRenderInterrupt = false,
  thread,
  threadMessages,
  modifyRunId,
  requestHelpEvents,
}: {
  message: Message | undefined;
  threadId: string;
  assistantId: string;
  forceRenderInterrupt?: boolean;
  thread: ReturnType<typeof useStream<Record<string, unknown>>>;
  threadMessages: Message[];
  modifyRunId?: (runId: string) => Promise<void>;
  requestHelpEvents?: CustomNodeEvent[];
}) {
  const content = message?.content ?? [];

  const handleHumanHelpResponse = async (response: string) => {
    const humanResponse: HumanResponse[] = [
      {
        type: "response",
        args: response,
      },
    ];

    const newRun = await thread.client.runs.create(threadId, assistantId, {
      command: { resume: humanResponse },
      config: {
        recursion_limit: 400,
      },
      streamResumable: true,
      streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
    });
    await modifyRunId?.(newRun.run_id);
  };

  const contentString = getContentString(content);
  const [hideToolCalls] = useQueryState(
    "hideToolCalls",
    parseAsBoolean.withDefault(false),
  );

  const messages = threadMessages;
  const idx = message ? messages.findIndex((m) => m.id === message.id) : -1;

  const threadInterrupt = thread.interrupt;
  const anthropicStreamedToolCalls = Array.isArray(content)
    ? parseAnthropicStreamedToolCalls(content)
    : undefined;

  const aiToolCalls: ToolCall[] = (() => {
    if (message && isAIMessageSDK(message)) {
      return message.tool_calls || [];
    }
    if (anthropicStreamedToolCalls?.length) {
      return anthropicStreamedToolCalls;
    }
    return [];
  })();

  const toolResults = aiToolCalls
    .map((toolCall) => {
      const matchingToolMessage = messages.find(
        (m) => isToolMessageSDK(m) && m.tool_call_id === toolCall.id,
      );

      return matchingToolMessage as ToolMessage | undefined;
    })
    .filter((m): m is ToolMessage => !!m);

  const actionableToolCalls = message
    ? aiToolCalls.filter(
        (tc) =>
          tc.name === shellTool.name ||
          tc.name === applyPatchTool.name ||
          tc.name === grepTool.name ||
          tc.name === installDependenciesTool.name ||
          tc.name === scratchpadTool.name ||
          tc.name === getURLContentTool.name ||
          tc.name === textEditorTool.name ||
          tc.name === viewTool.name ||
          tc.name === searchDocumentForTool.name ||
          isMcpTool(tc.name),
      )
    : [];

  const markTaskCompletedToolCall = message
    ? aiToolCalls.find((tc) => tc.name === markTaskCompletedTool.name)
    : undefined;

  const markTaskNotCompletedToolCall = message
    ? aiToolCalls.find((tc) => tc.name === markTaskNotCompletedTool.name)
    : undefined;

  const openPrToolCall = message
    ? aiToolCalls.find((tc) => tc.name === openPrTool.name)
    : undefined;

  const markFinalReviewTaskCompletedToolCall = message
    ? aiToolCalls.find(
        (tc) => tc.name === markFinalReviewTaskCompletedTool.name,
      )
    : undefined;

  const markFinalReviewTaskIncompleteToolCall = message
    ? aiToolCalls.find(
        (tc) => tc.name === markFinalReviewTaskIncompleteTool.name,
      )
    : undefined;

  const diagnoseErrorToolCall = message
    ? aiToolCalls.find((tc) => tc.name === diagnoseErrorTool.name)
    : undefined;

  const writeTechnicalNotesToolCall = message
    ? aiToolCalls.find((tc) => tc.name === writeTechnicalNotesTool.name)
    : undefined;

  const conversationHistorySummaryToolCall = message
    ? aiToolCalls.find((tc) => tc.name === conversationHistorySummaryTool.name)
    : undefined;

  const reviewStartedToolCall = message
    ? aiToolCalls.find((tc) => tc.name === reviewStartedTool.name)
    : undefined;

  const requestHumanHelpToolCall = message
    ? aiToolCalls.find((tc) => tc.name === requestHumanHelpTool.name)
    : undefined;

  // Check if this is a conversation history summary message
  if (conversationHistorySummaryToolCall && aiToolCalls.length === 1) {
    const correspondingToolResult = toolResults.find(
      (tr) => tr && tr.tool_call_id === conversationHistorySummaryToolCall.id,
    );

    return (
      <div className="flex flex-col gap-4">
        <ConversationHistorySummary
          summary={getMessageContentString(
            correspondingToolResult?.content ?? "",
          )}
        />
      </div>
    );
  }

  // Check if this is a review started message
  if (reviewStartedToolCall && aiToolCalls.length === 1) {
    const correspondingToolResult = toolResults.find(
      (tr) => tr && tr.tool_call_id === reviewStartedToolCall.id,
    );

    return (
      <div className="flex flex-col gap-4">
        <CodeReviewStarted
          status={correspondingToolResult ? "done" : "generating"}
        />
      </div>
    );
  }

  if (requestHumanHelpToolCall) {
    const correspondingToolResult = toolResults.find(
      (tr) => tr && tr.tool_call_id === requestHumanHelpToolCall.id,
    );

    const args = requestHumanHelpToolCall.args as RequestHumanHelpToolArgs;
    const reasoningText = getContentString(content);

    return (
      <div className="flex flex-col gap-4">
        <RequestHumanHelp
          status={correspondingToolResult ? "done" : "generating"}
          helpRequest={args.help_request}
          reasoningText={reasoningText}
          onSubmitResponse={handleHumanHelpResponse}
          requestHelpEvents={requestHelpEvents}
        />
      </div>
    );
  }

  // We can be sure that if either task status tool call is present, it will be the
  // only tool call/result we need to render for this message.
  if (markTaskCompletedToolCall || markTaskNotCompletedToolCall) {
    const toolCall = markTaskCompletedToolCall || markTaskNotCompletedToolCall;
    const completed = !!markTaskCompletedToolCall;

    const correspondingToolResult = toolResults.find(
      (tr) => tr && tr.tool_call_id === toolCall!.id,
    );

    const status = correspondingToolResult ? "done" : "generating";

    // Get the appropriate summary text based on which tool was called
    const summaryText = markTaskCompletedToolCall
      ? (markTaskCompletedToolCall.args as MarkTaskCompletedToolArgs)
          .completed_task_summary
      : (markTaskNotCompletedToolCall!.args as MarkTaskNotCompletedToolArgs)
          .reasoning;

    return (
      <div className="flex flex-col gap-4">
        <TaskSummary
          status={status}
          completed={completed}
          summaryText={summaryText}
        />
      </div>
    );
  }

  if (diagnoseErrorToolCall) {
    const correspondingToolResult = toolResults.find(
      (tr) => tr && tr.tool_call_id === diagnoseErrorToolCall.id,
    );

    const args = diagnoseErrorToolCall.args as DiagnoseErrorToolArgs;
    const reasoningText = getContentString(content);

    return (
      <div className="flex flex-col gap-4">
        <DiagnoseErrorAction
          status={correspondingToolResult ? "done" : "generating"}
          diagnosis={args.diagnosis}
          reasoningText={reasoningText}
        />
      </div>
    );
  }

  if (writeTechnicalNotesToolCall) {
    const correspondingToolResult = toolResults.find(
      (tr) => tr && tr.tool_call_id === writeTechnicalNotesToolCall.id,
    );

    const args =
      writeTechnicalNotesToolCall.args as WriteTechnicalNotesToolArgs;
    const reasoningText = getContentString(content);

    return (
      <div className="flex flex-col gap-4">
        <WriteTechnicalNotes
          status={correspondingToolResult ? "done" : "generating"}
          notes={args.notes}
          reasoningText={reasoningText}
        />
      </div>
    );
  }

  if (openPrToolCall) {
    let branch: string | undefined;
    let targetBranch: string | undefined = "main";

    if (message && isAIMessageSDK(message)) {
      branch = message.additional_kwargs?.branch as string | undefined;
      targetBranch =
        (message.additional_kwargs?.targetBranch as string | undefined) ||
        "main";
    }

    const args = openPrToolCall.args as OpenPrToolArgs;
    const correspondingToolResult = toolResults.find(
      (tr) => tr && tr.tool_call_id === openPrToolCall.id,
    );

    const status = correspondingToolResult ? "done" : "generating";

    const content = correspondingToolResult
      ? getContentString(correspondingToolResult.content)
      : "";

    // Extract PR URL from the tool message content
    // Format: "Created pull request: https://github.com/owner/repo/pull/123"
    // or "Marked pull request as ready for review: https://github.com/owner/repo/pull/123"
    let prUrl: string | undefined = undefined;
    if (content) {
      if (content.includes("pull request: ")) {
        prUrl = content.split("pull request: ")[1].trim();
      } else if (
        content.includes("Marked pull request as ready for review: ")
      ) {
        prUrl = content
          .split("Marked pull request as ready for review: ")[1]
          .trim();
      }
    }

    // Extract PR number from URL if available
    let prNumber: number | undefined = undefined;
    if (prUrl) {
      const match = prUrl.match(/\/pull\/(\d+)/);
      if (match && match[1]) {
        prNumber = parseInt(match[1], 10);
      }
    }

    return (
      <div className="flex flex-col gap-4">
        <PullRequestOpened
          status={status}
          title={args.title}
          description={args.body}
          url={prUrl}
          prNumber={prNumber}
          branch={branch}
          targetBranch={targetBranch}
          isDraft={content.includes("Opened draft")}
        />
      </div>
    );
  }

  // If task completed review tool call is present, render the task review component
  if (markFinalReviewTaskCompletedToolCall) {
    const args =
      markFinalReviewTaskCompletedToolCall.args as MarkFinalReviewTaskCompletedToolArgs;
    const correspondingToolResult = toolResults.find(
      (tr) => tr && tr.tool_call_id === markFinalReviewTaskCompletedToolCall.id,
    );

    const status = correspondingToolResult ? "done" : "generating";

    return (
      <div className="flex flex-col gap-4">
        <MarkTaskCompleted
          status={status}
          review={args.review}
          reasoningText={contentString}
        />
      </div>
    );
  }

  // If task incomplete review tool call is present, render the task review component
  if (markFinalReviewTaskIncompleteToolCall) {
    const args =
      markFinalReviewTaskIncompleteToolCall.args as MarkFinalReviewTaskIncompleteToolArgs;
    const correspondingToolResult = toolResults.find(
      (tr) =>
        tr && tr.tool_call_id === markFinalReviewTaskIncompleteToolCall.id,
    );

    const status = correspondingToolResult ? "done" : "generating";

    return (
      <div className="flex flex-col gap-4">
        <MarkTaskIncomplete
          status={status}
          review={args.review}
          additionalActions={args.additional_actions}
          reasoningText={contentString}
        />
      </div>
    );
  }

  if (actionableToolCalls.length > 0) {
    const actionItems = actionableToolCalls.map((toolCall): ActionItemProps => {
      const correspondingToolResult = toolResults.find(
        (tr) => tr && tr.tool_call_id === toolCall.id,
      );

      const isShellTool = toolCall.name === shellTool.name;
      const isGrepTool = toolCall.name === grepTool.name;
      const isInstallDependenciesTool =
        toolCall.name === installDependenciesTool.name;
      const isTextEditorTool = toolCall.name === textEditorTool.name;
      const isViewTool = toolCall.name === viewTool.name;

      if (correspondingToolResult) {
        // If we have a tool result, map it to action props
        return mapToolMessageToActionStepProps(
          correspondingToolResult,
          threadMessages,
        );
      } else if (isGrepTool) {
        const args = toolCall.args as GrepToolArgs;
        return {
          actionType: "grep",
          status: "generating",
          query: args?.query || "",
          match_string: args?.match_string || false,
          case_sensitive: args?.case_sensitive || false,
          context_lines: args?.context_lines || 0,
          max_results: args?.max_results || 0,
          follow_symlinks: args?.follow_symlinks || false,
          exclude_files: args?.exclude_files || [],
          include_files: args?.include_files || [],
          file_types: args?.file_types || [],
          output: "",
        } as ActionItemProps;
      } else if (isInstallDependenciesTool) {
        const args = toolCall.args as InstallDependenciesToolArgs;
        return {
          actionType: "install_dependencies",
          status: "generating",
          command: args?.command || "",
          workdir: args?.workdir || "",
          output: "",
        } as ActionItemProps;
      } else if (toolCall.name === scratchpadTool.name) {
        const args = toolCall.args as ScratchpadToolArgs;
        return {
          actionType: "scratchpad",
          status: "generating",
          scratchpad: args?.scratchpad || [],
        } as ActionItemProps;
      } else if (toolCall.name === getURLContentTool.name) {
        const args = toolCall.args as GetURLContentToolArgs;
        return {
          actionType: "get_url_content",
          status: "generating",
          url: args?.url || "",
          output: "",
        } as ActionItemProps;
      } else if (toolCall.name === searchDocumentForTool.name) {
        const args = toolCall.args as SearchDocumentForToolArgs;
        return {
          actionType: "search_document_for",
          status: "generating",
          url: args?.url || "",
          query: args?.query || "",
          output: "",
        } as ActionItemProps;
      } else if (isTextEditorTool) {
        const args = toolCall.args as TextEditorToolArgs;
        return {
          actionType: "text_editor",
          status: "generating",
          command: args?.command || "view",
          path: args?.path || "",
          view_range: args?.view_range,
          old_str: args?.old_str,
          new_str: args?.new_str,
          file_text: args?.file_text,
          insert_line: args?.insert_line,
          output: "",
        } as ActionItemProps;
      } else if (isViewTool) {
        const args = toolCall.args as ViewToolArgs;
        return {
          actionType: "text_editor",
          status: "generating",
          command: args?.command || "view",
          path: args?.path || "",
          view_range: args?.view_range,
          output: "",
        } as ActionItemProps;
      } else {
        if (isMcpTool(toolCall.name)) {
          return {
            actionType: "mcp",
            status: "generating",
            toolName: toolCall.name,
            args: toolCall.args as Record<string, any>,
          } as ActionItemProps;
        } else if (isShellTool) {
          const args = toolCall.args as ShellToolArgs;
          return {
            actionType: "shell",
            status: "generating",
            command: args?.command || [],
            workdir: args?.workdir,
            timeout: args?.timeout,
          } as ActionItemProps;
        } else {
          // Must be apply_patch tool
          const patchArgs = toolCall.args as ApplyPatchToolArgs;
          return {
            actionType: "apply-patch",
            status: "generating",
            file_path: patchArgs?.file_path || "",
            diff: patchArgs?.diff || "",
          } as ActionItemProps;
        }
      }
    });

    return (
      <div className="flex flex-col gap-4">
        <ActionStep
          actions={actionItems.filter(
            (item): item is ActionItemProps => item !== undefined,
          )}
          reasoningText={contentString}
        />
      </div>
    );
  }

  if (message?.type === "tool" && idx > 0) {
    const isPreviousToolCall = messages.slice(0, idx).some((prevMessage) => {
      if (isAIMessageSDK(prevMessage) && prevMessage.tool_calls) {
        return prevMessage.tool_calls.some(
          (tc) => tc.id === (message as ToolMessage).tool_call_id,
        );
      }

      if (Array.isArray(prevMessage.content)) {
        const toolCalls = parseAnthropicStreamedToolCalls(
          prevMessage.content as MessageContentComplex[],
        );
        return toolCalls?.some(
          (tc) => tc.id === (message as ToolMessage).tool_call_id,
        );
      }

      return false;
    });

    if (isPreviousToolCall) {
      return null;
    }
  }

  const isLastMessage =
    threadMessages[threadMessages.length - 1].id === message?.id;
  const hasNoAIOrToolMessages = !threadMessages.find(
    (m) => m.type === "ai" || m.type === "tool",
  );
  const isToolResult = message?.type === "tool";

  if (isToolResult && hideToolCalls) {
    return null;
  }

  return (
    <div className="group mr-auto flex w-full items-start gap-2">
      <div className="flex w-full flex-col gap-2">
        {isToolResult ? (
          <span>
            <ToolResult message={message} />
            <Interrupt
              interruptValue={threadInterrupt?.value}
              isLastMessage={isLastMessage}
              hasNoAIOrToolMessages={hasNoAIOrToolMessages}
              forceRenderInterrupt={forceRenderInterrupt}
              thread={thread}
            />
          </span>
        ) : (
          <span>
            {contentString.length > 0 && (
              <div className="py-1">
                <MarkdownText>{contentString}</MarkdownText>
              </div>
            )}

            {!hideToolCalls && aiToolCalls.length > 0 && (
              <span>
                <ToolCalls toolCalls={aiToolCalls} />
              </span>
            )}

            {message && (
              <CustomComponent
                message={message}
                thread={thread}
              />
            )}
            <Interrupt
              interruptValue={threadInterrupt?.value}
              isLastMessage={isLastMessage}
              hasNoAIOrToolMessages={hasNoAIOrToolMessages}
              forceRenderInterrupt={forceRenderInterrupt}
              thread={thread}
            />
          </span>
        )}
      </div>
    </div>
  );
}

export function AssistantMessageLoading() {
  return (
    <div className="mr-auto flex items-start gap-2">
      <div className="bg-muted flex h-8 items-center gap-1 rounded-2xl px-4 py-2">
        <div className="bg-foreground/50 h-1.5 w-1.5 animate-[pulse_1.5s_ease-in-out_infinite] rounded-full"></div>
        <div className="bg-foreground/50 h-1.5 w-1.5 animate-[pulse_1.5s_ease-in-out_0.5s_infinite] rounded-full"></div>
        <div className="bg-foreground/50 h-1.5 w-1.5 animate-[pulse_1.5s_ease-in-out_1s_infinite] rounded-full"></div>
      </div>
    </div>
  );
}
