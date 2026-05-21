import { useMemo } from "react";
import { CheckCircleIcon, ClockIcon } from "@phosphor-icons/react";

import { AgentGitPanel } from "@/components/agents/AgentGitPanel";
import { AgentPromptBar } from "@/components/agents/AgentPromptBar";
import { AgentsPageHeader, AgentsShell } from "@/components/agents/AgentsSidebar";
import { MessageView, summarizeChangedFiles } from "@/components/agents/ported";
import type { SessionUser } from "@/lib/api";
import type { AgentThread } from "@/lib/agents/types";
import { useSendAgentMessage } from "@/lib/agents/queries";
import { useAgentThreadStream } from "@/lib/agents/useThreadStream";

interface AgentThreadViewProps {
  user: SessionUser;
  thread: AgentThread;
}

export function AgentThreadView({ user, thread }: AgentThreadViewProps) {
  const sendMessage = useSendAgentMessage(thread.id);
  useAgentThreadStream(thread.id, thread.status === "running");

  const changedFiles = useMemo(() => {
    const agentMessages = thread.messages.filter((m) => m.author === "agent");
    const allChunks = agentMessages.flatMap((m) => m.chunks);
    return summarizeChangedFiles(allChunks);
  }, [thread.messages]);

  const hasMessages = thread.messages.length > 0;
  const isStreaming = thread.status === "running";

  return (
    <AgentsShell
      user={user}
      activeThreadId={thread.id}
      rightPanel={<AgentGitPanel thread={thread} />}
    >
      <div className="flex min-w-0 flex-1 flex-col">
        <AgentsPageHeader title={thread.title} subtitle={thread.repoFullName} />

        <div className="flex min-h-0 flex-1 flex-col">
          {hasMessages ? (
            <>
              <div className="min-h-0 flex-1 overflow-hidden px-6">
                <div className="mx-auto flex h-full max-w-3xl flex-col py-6">
                  <div className="mb-4 flex items-center gap-3 text-xs text-[var(--ui-text-dim)]">
                    <span className="inline-flex items-center gap-1 rounded-full border border-[var(--ui-border)] px-2 py-0.5">
                      <CheckCircleIcon className="size-3.5 text-[var(--ui-success)]" />
                      Environment ready
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <ClockIcon className="size-3.5" />
                      Worked for 3m 24s
                    </span>
                  </div>

                  <MessageView
                    messages={thread.messages}
                    isStreaming={isStreaming}
                    contentWidthClass="max-w-3xl"
                  />

                  {changedFiles.length > 0 && (
                    <div className="mt-4 rounded-lg border border-[var(--ui-border)] bg-[var(--ui-panel)] p-3">
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
                  )}
                </div>
              </div>

              <div className="shrink-0 border-t border-[var(--ui-border)] bg-[var(--ui-surface)] px-6 py-4">
                <div className="mx-auto max-w-3xl">
                  <AgentPromptBar
                    placeholder="Add a follow up"
                    compact
                    disabled={sendMessage.isPending}
                    onSubmit={(content) => sendMessage.mutate(content)}
                  />
                </div>
              </div>
            </>
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
