import { useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

import { AgentPromptBar } from "@/components/agents/AgentPromptBar";
import { AgentsShell } from "@/components/agents/AgentsSidebar";
import {
  MessageView,
  summarizeChangedFiles,
  type MessageViewScrollControl,
} from "@/components/agents/ported";
import type { SessionUser } from "@/lib/api";
import type { AgentThread } from "@/lib/agents/types";
import { useSendAgentMessage } from "@/lib/agents/queries";
import { useAgentThreadStream } from "@/lib/agents/useThreadStream";

interface AgentThreadViewProps {
  user: SessionUser;
  thread: AgentThread;
}

const PROMPT_OVERLAY_INSET = 128;

export function AgentThreadView({ user, thread }: AgentThreadViewProps) {
  const sendMessage = useSendAgentMessage(thread.id);
  useAgentThreadStream(thread.id, thread.status === "running");
  const scrollControlRef = useRef<MessageViewScrollControl | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  const changedFiles = useMemo(() => {
    const agentMessages = thread.messages.filter((m) => m.author === "agent");
    const allChunks = agentMessages.flatMap((m) => m.chunks);
    return summarizeChangedFiles(allChunks);
  }, [thread.messages]);

  const hasMessages = thread.messages.length > 0;
  const isStreaming = thread.status === "running";

  return (
    <AgentsShell user={user} activeThreadId={thread.id}>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 flex-col">
          {hasMessages ? (
            <div className="relative flex min-h-0 flex-1 flex-col">
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                <MessageView
                  messages={thread.messages}
                  isStreaming={isStreaming}
                  contentWidthClass="max-w-3xl"
                  bottomInset={PROMPT_OVERLAY_INSET}
                  scrollButtonSlot="external"
                  scrollControlRef={scrollControlRef}
                  onShowScrollToBottomChange={setShowScrollToBottom}
                />

                {changedFiles.length > 0 && (
                  <div className="mx-auto mt-4 w-full max-w-3xl shrink-0 px-6">
                    <div className="rounded-lg border border-[var(--ui-border)] bg-[var(--ui-panel)] p-3">
                      <div className="mb-2 text-xs font-medium text-[var(--ui-text-muted)]">
                        {changedFiles.length} Files Changed
                      </div>
                      <div className="space-y-1">
                        {changedFiles.map((file) => (
                          <div
                            key={file.filePath}
                            className="flex items-center justify-between rounded px-2 py-1 text-xs hover:bg-[var(--ui-panel-2)]"
                          >
                            <span className="font-mono text-[var(--ui-text)]">{file.filePath}</span>
                            <span className="text-[var(--ui-success)]">+{file.additions}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>

              <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 px-6 pb-4">
                <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-[var(--ui-bg)] via-[var(--ui-bg)]/80 to-transparent" />
                <div className="pointer-events-auto relative mx-auto max-w-3xl">
                  {showScrollToBottom && (
                    <button
                      type="button"
                      onClick={() => scrollControlRef.current?.scrollToBottom()}
                      aria-label="Scroll to bottom"
                      className="absolute bottom-full left-1/2 z-30 mb-2 inline-flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full bg-[var(--ui-panel-2)] text-[color:var(--ui-text-muted)] shadow-md transition-colors hover:bg-[var(--ui-panel)] hover:text-[color:var(--ui-text)]"
                    >
                      <ChevronDown className="h-3.5 w-3.5" />
                    </button>
                  )}
                  <AgentPromptBar
                    placeholder="Add a follow up"
                    compact
                    busy={isStreaming}
                    disabled={sendMessage.isPending}
                    onSubmit={(content) => sendMessage.mutate(content)}
                  />
                </div>
              </div>
            </div>
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6">
              <p className="text-sm text-[var(--ui-text-dim)]">
                {isStreaming ? "Agent is starting…" : "This thread has no messages yet."}
              </p>
              {!isStreaming && (
                <div className="w-full max-w-3xl">
                  <AgentPromptBar
                    placeholder="Send the first message"
                    compact
                    busy={isStreaming}
                    disabled={sendMessage.isPending}
                    onSubmit={(content) => sendMessage.mutate(content)}
                  />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </AgentsShell>
  );
}
