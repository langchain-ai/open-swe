import { useEffect, useMemo, useState } from "react";

import type { SessionUser } from "@/lib/api";
import type { PendingPrompt } from "@/lib/agents/pendingPrompts";
import type { AgentThread, Message } from "@/lib/agents/types";
import type { ModelSelection } from "@/lib/agents/useModelOptions";
import { AgentPromptBar } from "@/components/agents/AgentPromptBar";
import { AgentsShell } from "@/components/agents/AgentsSidebar";
import { MessageView } from "@/components/agents/ported";
import { useCancelAgentThread, useSendAgentMessage } from "@/lib/agents/queries";
import { dropPendingPrompts, getPendingPrompts } from "@/lib/agents/pendingPrompts";
import { useAgentThreadStream } from "@/lib/agents/useThreadStream";
import { useModelOptions } from "@/lib/agents/useModelOptions";

interface AgentThreadViewProps {
  user: SessionUser;
  thread: AgentThread;
}

export function AgentThreadView({ user, thread }: AgentThreadViewProps) {
  const sendMessage = useSendAgentMessage(thread.id);
  const cancelThread = useCancelAgentThread(thread.id);
  useAgentThreadStream(thread.id, thread.status === "running");
  const [pendingPrompts, setPendingPrompts] = useState<Array<PendingPrompt>>(() =>
    getPendingPrompts(thread.id),
  );

  const { models, defaultSelection } = useModelOptions();
  const threadSelection = useMemo<ModelSelection | null>(() => {
    if (!thread.model || !thread.effort) return null;
    const supported = models.some(
      (m) => m.id === thread.model && m.efforts.includes(thread.effort ?? ""),
    );
    if (!supported) return null;
    return { modelId: thread.model, effort: thread.effort };
  }, [models, thread.model, thread.effort]);
  const [selection, setSelection] = useState<ModelSelection | null>(null);
  const activeSelection = selection ?? threadSelection ?? defaultSelection;

  const userMessageTexts = useMemo(() => {
    return new Set(
      thread.messages
        .filter((m) => m.author === "user")
        .map((m) =>
          m.chunks
            .filter((c) => c.kind === "text")
            .map((c) => c.text)
            .join(""),
        ),
    );
  }, [thread.messages]);

  useEffect(() => {
    setPendingPrompts((prev) => {
      if (prev.length === 0) return prev;
      const next = dropPendingPrompts(thread.id, (entry) =>
        userMessageTexts.has(entry.prompt),
      );
      return next.length === prev.length ? prev : next;
    });
  }, [thread.id, userMessageTexts]);

  const displayMessages = useMemo<Array<Message>>(() => {
    if (pendingPrompts.length === 0) return thread.messages;
    const baseTimestamp = new Date().toISOString();
    const result = thread.messages.slice();
    pendingPrompts.forEach((entry, i) => {
      const synth: Message = {
        id: `pending-user-${i}`,
        author: "user",
        timestamp: baseTimestamp,
        chunks: [{ kind: "text", text: entry.prompt }],
      };
      const at = Math.min(Math.max(entry.insertAt, 0), result.length);
      result.splice(at, 0, synth);
    });
    return result;
  }, [thread.messages, pendingPrompts]);

  const hasMessages = displayMessages.length > 0;
  const hasActiveRun = thread.status === "running";
  const isStreaming = hasActiveRun || pendingPrompts.length > 0;

  return (
    <AgentsShell user={user} activeThreadId={thread.id}>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 flex-col">
          {hasMessages ? (
            <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
              <MessageView
                messages={displayMessages}
                isStreaming={isStreaming}
                contentWidthClass="max-w-3xl"
              />
              <div className="shrink-0 px-4 pb-4">
                <div className="mx-auto w-full min-w-0 max-w-3xl">
                  <AgentPromptBar
                    placeholder="Add a follow up"
                    compact
                    busy={hasActiveRun}
                    disabled={sendMessage.isPending}
                    onSubmit={(content) =>
                      sendMessage.mutate({
                        content,
                        model_id: activeSelection?.modelId ?? null,
                        effort: activeSelection?.effort ?? null,
                      })
                    }
                    onStop={() => cancelThread.mutate()}
                    stopping={cancelThread.isPending}
                    models={models}
                    selection={activeSelection}
                    onSelectionChange={setSelection}
                  />
                </div>
              </div>
            </div>
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6">
              <p className="text-sm text-[var(--ui-text-dim)]">This thread has no messages yet.</p>
              <div className="w-full max-w-3xl">
                <AgentPromptBar
                  placeholder="Send the first message"
                  compact
                  busy={hasActiveRun}
                  disabled={sendMessage.isPending}
                  onSubmit={(content) =>
                    sendMessage.mutate({
                      content,
                      model_id: activeSelection?.modelId ?? null,
                      effort: activeSelection?.effort ?? null,
                    })
                  }
                  onStop={() => cancelThread.mutate()}
                  stopping={cancelThread.isPending}
                  models={models}
                  selection={activeSelection}
                  onSelectionChange={setSelection}
                />
              </div>
            </div>
          )}
        </div>
      </div>
    </AgentsShell>
  );
}
