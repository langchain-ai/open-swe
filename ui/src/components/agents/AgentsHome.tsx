import { Link } from "@tanstack/react-router";

import { AgentPromptBar } from "@/components/agents/AgentPromptBar";
import { AgentRunCard } from "@/components/agents/AgentRunCard";
import { Logo } from "@/components/agents/ported/Logo";
import { useAgentThreads, useCreateAgentThread } from "@/lib/agents/queries";

export function AgentsHome() {
  const threadsQuery = useAgentThreads();
  const createThread = useCreateAgentThread();
  const recentRuns = threadsQuery.data ?? [];

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto px-6 py-8">
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col items-center justify-center">
        <div className="flex w-full flex-col items-center gap-6">
          <Logo />
          <AgentPromptBar
            onSubmit={(prompt) => createThread.mutate({ prompt })}
            disabled={createThread.isPending}
          />
        </div>

        <div className="mx-auto mt-6 w-full max-w-[640px] space-y-2">
          {threadsQuery.isLoading ? (
            <p className="text-center text-sm text-[var(--ui-text-dim)]">Loading agents…</p>
          ) : recentRuns.length === 0 ? (
            <AgentsHomeEmptyState />
          ) : (
            recentRuns.map((thread) => <AgentRunCard key={thread.id} thread={thread} />)
          )}
        </div>
      </div>
    </div>
  );
}

export function AgentsHomeEmptyState() {
  return (
    <div className="flex flex-col items-center gap-3 py-4 text-center">
      <p className="text-sm text-[var(--ui-text-muted)]">No agents yet.</p>
      <Link
        to="/agents"
        className="text-sm font-medium text-[var(--ui-accent)] hover:underline"
      >
        Start your first agent
      </Link>
    </div>
  );
}
