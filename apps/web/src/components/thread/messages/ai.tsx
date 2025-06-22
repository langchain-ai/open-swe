import { parsePartialJson } from "@langchain/core/output_parsers";
import {
  AIMessage,
  Checkpoint,
  Message,
  ToolMessage,
} from "@langchain/langgraph-sdk";
import { getContentString } from "../utils";
import { BranchSwitcher, CommandBar } from "./shared";
import { MarkdownText } from "../markdown-text";
import {
  LoadExternalComponent,
  UIMessage,
} from "@langchain/langgraph-sdk/react-ui";
import { cn } from "@/lib/utils";
import { ToolCalls, ToolResult } from "./tool-calls";
import { MessageContentComplex } from "@langchain/core/messages";
import { Fragment } from "react/jsx-runtime";
import { useQueryState, parseAsBoolean } from "nuqs";
import { Interrupt } from "./interrupt";
import { ActionStep, ActionItemProps } from "@/components/gen-ui/action-step";
import { TaskSummary } from "@/components/gen-ui/task-summary";
import { ToolCall } from "@langchain/core/messages/tool";
import {
  createApplyPatchToolFields,
  createShellToolFields,
  createSetTaskStatusToolFields,
  createRgToolFields,
} from "@open-swe/shared/open-swe/tools";
import { z } from "zod";
import { isAIMessageSDK, isToolMessageSDK } from "@/lib/langchain-messages";
import { useStream } from "@langchain/langgraph-sdk/react";

// Used only for Zod type inference.
const dummyRepo = { owner: "dummy", repo: "dummy" };
const shellTool = createShellToolFields(dummyRepo);
type ShellToolArgs = z.infer<typeof shellTool.schema>;
const applyPatchTool = createApplyPatchToolFields(dummyRepo);
type ApplyPatchToolArgs = z.infer<typeof applyPatchTool.schema>;
const setTaskStatusTool = createSetTaskStatusToolFields();
type SetTaskStatusToolArgs = z.infer<typeof setTaskStatusTool.schema>;
const rgTool = createRgToolFields(dummyRepo);
type RgToolArgs = z.infer<typeof rgTool.schema>;

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
  thread: { messages: Message[] },
): ActionItemProps {
  const toolCall: ToolCall | undefined = thread.messages
    .filter(isAIMessageSDK)
    .flatMap((m) => m.tool_calls ?? [])
    .find((tc) => tc.id === message.tool_call_id);

  const aiMessage = thread.messages
    .filter(isAIMessageSDK)
    .find((m) => m.tool_calls?.some((tc) => tc.id === message.tool_call_id));
  const reasoningText = aiMessage
    ? getContentString(aiMessage.content)
    : undefined;

  const status: ActionItemProps["status"] = "done";
  const success = message.status === "success";

  if (toolCall?.name === shellTool.name) {
    const args = toolCall.args as ShellToolArgs;
    return {
      actionType: shellTool.name as "shell",
      status,
      success,
      command: args.command || [],
      workdir: args.workdir,
      output: getContentString(message.content),
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
  } else if (toolCall?.name === rgTool.name) {
    const args = toolCall.args as RgToolArgs;
    return {
      actionType: "rg",
      status,
      success,
      pattern: args.pattern || "",
      paths: args.paths || [],
      output: getContentString(message.content),
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
  isLoading,
  handleRegenerate,
  forceRenderInterrupt = false,
  thread,
}: {
  message: Message | undefined;
  isLoading: boolean;
  handleRegenerate: (parentCheckpoint: Checkpoint | null | undefined) => void;
  forceRenderInterrupt?: boolean;
  thread: ReturnType<typeof useStream<Record<string, unknown>>>;
}) {
  const content = message?.content ?? [];
  const contentString = getContentString(content);
  const [hideToolCalls] = useQueryState(
    "hideToolCalls",
    parseAsBoolean.withDefault(false),
  );

  const messages = thread.messages;
  const idx = message ? messages.findIndex((m) => m.id === message.id) : -1;

  const meta = message ? thread.getMessagesMetadata(message) : undefined;
  const threadInterrupt = thread.interrupt;
  const parentCheckpoint = meta?.firstSeenState?.parent_checkpoint;
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
          tc.name === rgTool.name,
      )
    : [];

  const taskStatusToolCall = message
    ? aiToolCalls.find((tc) => tc.name === setTaskStatusTool.name)
    : undefined;

  // We can be sure that if the task status tool call is present, it will be the
  // only tool call/result we need to render for this message.
  if (taskStatusToolCall) {
    const args = taskStatusToolCall.args as SetTaskStatusToolArgs;
    const correspondingToolResult = toolResults.find(
      (tr) => tr && tr.tool_call_id === taskStatusToolCall.id,
    );

    const status = correspondingToolResult ? "done" : "generating";
    const completed = args.task_status === "completed";

    return (
      <div className="flex flex-col gap-4">
        <TaskSummary
          status={status}
          completed={completed}
          summaryText={args.reasoning}
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
      const isRgTool = toolCall.name === rgTool.name;

      if (correspondingToolResult) {
        // If we have a tool result, map it to action props
        return mapToolMessageToActionStepProps(correspondingToolResult, thread);
      } else if (isRgTool) {
        const args = toolCall.args as RgToolArgs;
        return {
          actionType: "rg",
          status: "generating",
          pattern: args?.pattern || "",
          paths: args?.paths || [],
          output: "",
        } as ActionItemProps;
      } else {
        if (isShellTool) {
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
    thread.messages[thread.messages.length - 1].id === message?.id;
  const hasNoAIOrToolMessages = !thread.messages.find(
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
            <div
              className={cn(
                "mr-auto flex items-center gap-2 transition-opacity",
                "opacity-0 group-focus-within:opacity-100 group-hover:opacity-100",
              )}
            >
              <BranchSwitcher
                branch={meta?.branch}
                branchOptions={meta?.branchOptions}
                onSelect={(branch) => thread.setBranch(branch)}
                isLoading={isLoading}
              />
              <CommandBar
                content={contentString}
                isLoading={isLoading}
                isAiMessage={true}
                handleRegenerate={() => handleRegenerate(parentCheckpoint)}
              />
            </div>
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
