import { Link } from "@tanstack/react-router";
import { CheckCircleIcon, GitBranchIcon, GitPullRequestIcon } from "@phosphor-icons/react";

import { formatRelativeTime } from "@/lib/agents/api";
import type { AgentThread } from "@/lib/agents/types";
import { cn } from "@/lib/utils";

interface AgentRunCardProps {
  thread: AgentThread;
}

export function AgentRunCard({ thread }: AgentRunCardProps) {
  const stats = thread.diffStats;
  const hasPr = Boolean(thread.pr);

  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId: thread.id }}
      className="group flex items-center gap-4 rounded-xl border border-[var(--ui-border)] bg-[var(--ui-surface)] px-4 py-3 transition-colors hover:border-[var(--ui-accent)]/30 hover:bg-[var(--ui-panel)]"
    >
      <div className="flex size-[72px] shrink-0 flex-col items-center justify-center rounded-lg border border-[var(--ui-border)] bg-[var(--ui-panel-2)] text-center">
        {stats ? (
          <>
            <div className="text-[11px] font-medium text-[var(--ui-text-muted)]">
              {stats.files} {stats.files === 1 ? "file" : "files"}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-xs font-medium">
              <span className="text-[var(--ui-success)]">+{stats.additions}</span>
              <span className="text-[var(--ui-danger)]">-{stats.deletions}</span>
            </div>
          </>
        ) : (
          <GitBranchIcon className="size-5 text-[var(--ui-text-dim)]" />
        )}
        <div className="mt-1.5 flex items-center gap-1 text-[10px] text-[var(--ui-text-dim)]">
          {hasPr ? (
            <>
              <GitPullRequestIcon className="size-3" />
              Draft
            </>
          ) : (
            <>
              <GitBranchIcon className="size-3" />
              Branch
            </>
          )}
        </div>
      </div>

      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-[var(--ui-text)]">{thread.title}</div>
        <div className="mt-1 flex items-center gap-2 text-xs text-[var(--ui-text-dim)]">
          <span>{thread.model}</span>
          <span>·</span>
          <span>{thread.repo}</span>
          <span>·</span>
          <span>{formatRelativeTime(thread.updatedAt)}</span>
        </div>
      </div>

      <CheckCircleIcon
        className={cn(
          "size-5 shrink-0",
          thread.status === "finished"
            ? "text-[var(--ui-text-dim)] opacity-100"
            : "opacity-0",
        )}
        weight="regular"
      />
    </Link>
  );
}
