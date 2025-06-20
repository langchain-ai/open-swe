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
import {
  ActionStep,
  type ActionStepProps,
} from "@/components/gen-ui/action-step";
import { ToolCall } from "@langchain/core/messages/tool";
import {
  createApplyPatchToolFields,
  createShellToolFields,
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
): ActionStepProps {
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

  const status: ActionStepProps["status"] = "done";
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
      reasoningText,
      pattern: args.pattern,
      paths: args.paths,
      output: getContentString(message.content),
      flags: args.flags,
    };
  }
  return {
    status: "loading",
    summaryText: reasoningText,
  };
}

function mapToolCallToActionStepProps(
  toolCallName: string,
  toolCallArgs:
    | Partial<ShellToolArgs | ApplyPatchToolArgs | RgToolArgs>
    | undefined,
): ActionStepProps {
  if (toolCallName === shellTool.name) {
    const args = toolCallArgs as ShellToolArgs;
    return {
      actionType: "shell",
      status: "generating",
      command: args.command || [],
      workdir: args.workdir,
    };
  } else if (toolCallName === applyPatchTool.name) {
    const args = toolCallArgs as ApplyPatchToolArgs;
    return {
      actionType: "apply-patch",
      status: "generating",
      file_path: args.file_path || "",
      diff: args.diff,
    };
  } else if (toolCallName === rgTool.name) {
    const args = toolCallArgs as RgToolArgs;
    return {
      actionType: "rg",
      status: "generating",
      pattern: args.pattern,
      paths: args.paths,
      flags: args.flags,
    };
  }

  return {
    status: "loading",
    summaryText: "",
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
  const nextMessage = idx >= 0 ? messages[idx + 1] : undefined;

  const meta = message ? thread.getMessagesMetadata(message) : undefined;
  const threadInterrupt = thread.interrupt;
  const parentCheckpoint = meta?.firstSeenState?.parent_checkpoint;
  const anthropicStreamedToolCalls = Array.isArray(content)
    ? parseAnthropicStreamedToolCalls(content)
    : undefined;

  // Helper: get tool call name from AI message (OpenAI or Anthropic)
  const aiToolCallName = (() => {
    if (message && isAIMessageSDK(message)) {
      return message.tool_calls?.[0]?.name;
    }
    if (anthropicStreamedToolCalls?.length) {
      return anthropicStreamedToolCalls[anthropicStreamedToolCalls.length - 1]
        .name;
    }
    return undefined;
  })();

  const aiToolCallArgs = (() => {
    if (message && isAIMessageSDK(message)) {
      return message.tool_calls?.[0]?.args as
        | Partial<ShellToolArgs | ApplyPatchToolArgs | RgToolArgs>
        | undefined;
    }
    if (anthropicStreamedToolCalls?.length) {
      return anthropicStreamedToolCalls[anthropicStreamedToolCalls.length - 1]
        .args as
        | Partial<ShellToolArgs | ApplyPatchToolArgs | RgToolArgs>
        | undefined;
    }
    return undefined;
  })();

  const toolResult =
    nextMessage &&
    isToolMessageSDK(nextMessage) &&
    aiToolCallName &&
    nextMessage.tool_call_id ===
      (message && isAIMessageSDK(message)
        ? message.tool_calls?.[0]?.id
        : anthropicStreamedToolCalls?.length
          ? anthropicStreamedToolCalls[anthropicStreamedToolCalls.length - 1].id
          : undefined)
      ? nextMessage
      : undefined;

  if (
    message &&
    (aiToolCallName === shellTool.name ||
      aiToolCallName === applyPatchTool.name ||
      aiToolCallName === rgTool.name)
  ) {
    if (toolResult) {
      return (
        <ActionStep {...mapToolMessageToActionStepProps(toolResult, thread)} />
      );
    }
    return (
      <ActionStep
        {...mapToolCallToActionStepProps(aiToolCallName, aiToolCallArgs)}
        reasoningText={contentString}
      />
    );
  }

  if (
    message?.type === "tool" &&
    (message.name === shellTool.name ||
      message.name === applyPatchTool.name ||
      message.name === rgTool.name) &&
    idx > 0 &&
    messages[idx - 1] &&
    ((messages[idx - 1] &&
      isAIMessageSDK(messages[idx - 1]) &&
      (messages[idx - 1] as AIMessage).tool_calls?.some(
        (tc) =>
          tc.id === (message as ToolMessage).tool_call_id &&
          (tc.name === shellTool.name ||
            tc.name === applyPatchTool.name ||
            tc.name === rgTool.name),
      )) ||
      (Array.isArray(messages[idx - 1].content) &&
        parseAnthropicStreamedToolCalls(
          messages[idx - 1].content as MessageContentComplex[],
        )?.some(
          (tc) =>
            tc.id === (message as ToolMessage).tool_call_id &&
            (tc.name === shellTool.name ||
              tc.name === applyPatchTool.name ||
              tc.name === rgTool.name),
        )))
  ) {
    return null;
  }

  const isLastMessage =
    thread.messages[thread.messages.length - 1].id === message?.id;
  const hasNoAIOrToolMessages = !thread.messages.find(
    (m) => m.type === "ai" || m.type === "tool",
  );

  const hasToolCalls =
    message &&
    "tool_calls" in message &&
    message.tool_calls &&
    message.tool_calls.length > 0;
  const toolCallsHaveContents =
    hasToolCalls &&
    message.tool_calls?.some(
      (tc) => tc.args && Object.keys(tc.args).length > 0,
    );
  const hasAnthropicToolCalls = !!anthropicStreamedToolCalls?.length;
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

            {!hideToolCalls && (
              <span>
                {(hasToolCalls && toolCallsHaveContents && (
                  <ToolCalls toolCalls={message.tool_calls} />
                )) ||
                  (hasAnthropicToolCalls && (
                    <ToolCalls toolCalls={anthropicStreamedToolCalls} />
                  )) ||
                  (hasToolCalls && (
                    <ToolCalls toolCalls={message.tool_calls} />
                  ))}
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
