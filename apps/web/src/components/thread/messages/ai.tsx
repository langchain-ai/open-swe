import { parsePartialJson } from "@langchain/core/output_parsers";
import { useStreamContext } from "@/providers/Stream";
import {
  AIMessage,
  Checkpoint,
  Message,
  ToolMessage,
} from "@langchain/langgraph-sdk";
import { getContentString } from "../utils";
import { BranchSwitcher, CommandBar } from "./shared";
import { MarkdownText } from "../markdown-text";
import { LoadExternalComponent } from "@langchain/langgraph-sdk/react-ui";
import { cn } from "@/lib/utils";
import { ToolCalls, ToolResult } from "./tool-calls";
import { MessageContentComplex } from "@langchain/core/messages";
import { Fragment } from "react/jsx-runtime";
import { useQueryState, parseAsBoolean } from "nuqs";
import { useArtifact } from "../artifact";
import { Interrupt } from "./interrupt";
import {
  ActionStep,
  type ActionStepProps,
} from "@/components/gen-ui/action-step";
import { getMessageContentString } from "@open-swe/shared/messages";
import { ToolCall } from "@langchain/core/messages/tool";
import {
  createApplyPatchToolFields,
  createShellToolFields,
} from "@open-swe/shared/open-swe/tools";
import { z } from "zod";

// Used only for Zod type inference.
const dummyRepo = { owner: "dummy", repo: "dummy" };
const shellSchema = createShellToolFields(dummyRepo).schema;
type ShellToolArgs = z.infer<typeof shellSchema>;
const applyPatchSchema = createApplyPatchToolFields(dummyRepo).schema;
type ApplyPatchToolArgs = z.infer<typeof applyPatchSchema>;

function CustomComponent({
  message,
  thread,
}: {
  message: Message;
  thread: ReturnType<typeof useStreamContext>;
}) {
  const artifact = useArtifact();
  const { values } = useStreamContext();
  const customComponents = values.ui?.filter(
    (ui) => ui.metadata?.message_id === message.id,
  );

  if (!customComponents?.length) return null;
  return (
    <Fragment key={message.id}>
      {customComponents.map((customComponent) => (
        <LoadExternalComponent
          key={customComponent.id}
          stream={thread}
          message={customComponent}
          meta={{ ui: customComponent, artifact }}
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

// Local type guard for AIMessage, isAIMessage is for type BaseMessage
function isAIMessageLocal(m: Message): m is AIMessage {
  return m.type === "ai";
}

export function mapToolMessageToActionStepProps(
  message: ToolMessage,
  thread: { messages: Message[] },
): ActionStepProps {
  const toolCall: ToolCall | undefined = thread.messages
    .filter(isAIMessageLocal)
    .flatMap((m) => m.tool_calls ?? [])
    .find((tc) => tc.id === message.tool_call_id);

  const aiMessage = thread.messages
    .filter(isAIMessageLocal)
    .find((m) => m.tool_calls?.some((tc) => tc.id === message.tool_call_id));
  const reasoningText = aiMessage
    ? getContentString(aiMessage.content)
    : undefined;

  const status: ActionStepProps["status"] = "done";
  const success = message.status === "success";

  if (toolCall?.name === "shell") {
    const args = toolCall.args as ShellToolArgs;
    return {
      actionType: "shell",
      status,
      success,
      command: args.command || [],
      workdir: args.workdir,
      output: getMessageContentString(message.content),
      reasoningText,
    };
  } else if (toolCall?.name === "apply_patch") {
    const args = toolCall.args as ApplyPatchToolArgs;
    return {
      actionType: "apply-patch",
      status,
      success,
      file: args.file_path || "",
      diff: args.diff,
      reasoningText,
      errorMessage: !success
        ? getMessageContentString(message.content)
        : undefined,
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
}: {
  message: Message | undefined;
  isLoading: boolean;
  handleRegenerate: (parentCheckpoint: Checkpoint | null | undefined) => void;
  forceRenderInterrupt?: boolean;
}) {
  const content = message?.content ?? [];
  const contentString = getContentString(content);
  const [hideToolCalls] = useQueryState(
    "hideToolCalls",
    parseAsBoolean.withDefault(false),
  );

  const thread = useStreamContext();
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
    if (message && isAIMessageLocal(message)) {
      return message.tool_calls?.[0]?.name;
    }
    if (anthropicStreamedToolCalls?.length) {
      return anthropicStreamedToolCalls[anthropicStreamedToolCalls.length - 1]
        .name;
    }
    return undefined;
  })();

  const aiToolCallArgs = (() => {
    if (message && isAIMessageLocal(message)) {
      return message.tool_calls?.[0]?.args;
    }
    if (anthropicStreamedToolCalls?.length) {
      return anthropicStreamedToolCalls[anthropicStreamedToolCalls.length - 1]
        .args;
    }
    return undefined;
  })();

  const toolResult =
    nextMessage &&
    nextMessage.type === "tool" &&
    aiToolCallName &&
    (nextMessage as ToolMessage).tool_call_id ===
      (message && isAIMessageLocal(message)
        ? message.tool_calls?.[0]?.id
        : anthropicStreamedToolCalls?.length
          ? anthropicStreamedToolCalls[anthropicStreamedToolCalls.length - 1].id
          : undefined)
      ? (nextMessage as ToolMessage)
      : undefined;

  if (
    message &&
    (aiToolCallName === "shell" || aiToolCallName === "apply_patch")
  ) {
    if (toolResult) {
      return (
        <ActionStep {...mapToolMessageToActionStepProps(toolResult, thread)} />
      );
    }
    return (
      <ActionStep
        actionType={aiToolCallName === "shell" ? "shell" : "apply-patch"}
        status="generating"
        command={
          aiToolCallName === "shell" ? aiToolCallArgs?.command || [] : undefined
        }
        workdir={
          aiToolCallName === "shell" ? aiToolCallArgs?.workdir : undefined
        }
        file={
          aiToolCallName === "apply_patch"
            ? aiToolCallArgs?.file_path || ""
            : undefined
        }
        diff={
          aiToolCallName === "apply_patch" ? aiToolCallArgs?.diff : undefined
        }
        reasoningText={contentString}
      />
    );
  }

  if (
    message?.type === "tool" &&
    (message.name === "shell" || message.name === "apply_patch") &&
    idx > 0 &&
    messages[idx - 1] &&
    ((messages[idx - 1] &&
      isAIMessageLocal(messages[idx - 1]) &&
      (messages[idx - 1] as AIMessage).tool_calls?.some(
        (tc) =>
          tc.id === (message as ToolMessage).tool_call_id &&
          (tc.name === "shell" || tc.name === "apply_patch"),
      )) ||
      (Array.isArray(messages[idx - 1].content) &&
        parseAnthropicStreamedToolCalls(
          messages[idx - 1].content as MessageContentComplex[],
        )?.some(
          (tc) =>
            tc.id === (message as ToolMessage).tool_call_id &&
            (tc.name === "shell" || tc.name === "apply_patch"),
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
    <div className="group mr-auto flex w-full max-w-3xl items-start gap-2">
      <div className="flex w-full flex-col gap-2">
        {isToolResult ? (
          <span>
            {(() => {
              const toolCall = thread.messages
                .filter(isAIMessageLocal)
                .flatMap((m) => m.tool_calls ?? [])
                .find((tc) => tc.id === (message as ToolMessage).tool_call_id);
              if (
                toolCall?.name === "shell" ||
                toolCall?.name === "apply_patch"
              ) {
                return (
                  <ActionStep
                    {...mapToolMessageToActionStepProps(
                      message as ToolMessage,
                      thread,
                    )}
                  />
                );
              }
              return <ToolResult message={message} />;
            })()}
            <Interrupt
              interruptValue={threadInterrupt?.value}
              isLastMessage={isLastMessage}
              hasNoAIOrToolMessages={hasNoAIOrToolMessages}
              forceRenderInterrupt={forceRenderInterrupt}
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
