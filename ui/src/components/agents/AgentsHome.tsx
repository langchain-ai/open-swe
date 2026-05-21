import { Link } from "@tanstack/react-router";
import { CommandIcon, RobotIcon } from "@phosphor-icons/react";

import { AgentPromptBar } from "@/components/agents/AgentPromptBar";
import { AgentRunCard } from "@/components/agents/AgentRunCard";
import { AgentsPageHeader } from "@/components/agents/AgentsSidebar";
import { MOCK_THREADS } from "@/lib/agents/mock-data";

export function AgentsHome() {
  const recentRuns = [...MOCK_THREADS].sort((a, b) => b.updatedAt - a.updatedAt);

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <AgentsPageHeader />

      <div className="flex min-h-0 flex-1 flex-col items-center overflow-y-auto px-6 py-10">
        <div className="flex w-full max-w-2xl flex-col items-center">
          <AgentPromptBar />

          <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded-full border border-[var(--ui-border)] bg-[var(--ui-surface)] px-3 py-1.5 text-xs text-[var(--ui-text-muted)] hover:bg-[var(--ui-panel-2)]"
            >
              <RobotIcon className="size-3.5" />
              Create an Automation
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded-full border border-[var(--ui-border)] bg-[var(--ui-surface)] px-3 py-1.5 text-xs text-[var(--ui-text-muted)] hover:bg-[var(--ui-panel-2)]"
            >
              <CommandIcon className="size-3.5" />
              Try Commands
              <span className="rounded bg-[var(--ui-panel-2)] px-1 py-0.5 text-[10px]">Press /</span>
            </button>
          </div>
        </div>

        <div className="mt-10 w-full max-w-2xl space-y-2">
          {recentRuns.map((thread) => (
            <AgentRunCard key={thread.id} thread={thread} />
          ))}
        </div>
      </div>
    </div>
  );
}

export function AgentsHomeEmptyState() {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
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
