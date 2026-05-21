import { Link } from "@tanstack/react-router";
import { ChartLineUpIcon, GearIcon, PlusIcon } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import type { SessionUser } from "@/lib/api";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { groupThreads } from "@/lib/agents/api";
import { useAgentThreads } from "@/lib/agents/queries";
import type { AgentThread } from "@/lib/agents/types";
import { cn } from "@/lib/utils";

interface AgentsSidebarProps {
  user: SessionUser;
  activeThreadId?: string;
}

const NAV = [{ to: "/my-settings", label: "Dashboard", icon: ChartLineUpIcon }] as const;

export function AgentsSidebar({ user, activeThreadId }: AgentsSidebarProps) {
  const threadsQuery = useAgentThreads();
  const threads = threadsQuery.data ?? [];
  const groups = groupThreads(threads);
  const initials = (user.login || "?").slice(0, 2).toUpperCase();

  return (
    <aside className="flex h-full w-[260px] shrink-0 flex-col border-r border-[var(--ui-border)] bg-[var(--ui-sidebar)]">
      <div className="px-3 pt-4 pb-3">
        <Link
          to="/agents"
          className="flex w-full items-center gap-2 rounded-lg border border-[var(--ui-border)] bg-[var(--ui-surface)] px-3 py-2 text-sm font-medium text-[var(--ui-text)] shadow-sm transition-colors hover:bg-[var(--ui-panel-2)]"
        >
          <PlusIcon className="size-4" weight="bold" />
          New Agent
        </Link>
      </div>

      <nav className="flex flex-col gap-0.5 px-2 pb-4">
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <Link
              key={item.to}
              to={item.to}
              className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs text-[var(--ui-text-muted)] transition-colors hover:bg-[var(--ui-sidebar-hover)] hover:text-[var(--ui-text)]"
            >
              <Icon className="size-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <ThreadGroup label="Today" threads={groups.today} activeThreadId={activeThreadId} />
        <ThreadGroup label="Last 30 days" threads={groups.last30} activeThreadId={activeThreadId} />
        <ThreadGroup label="Older" threads={groups.older} activeThreadId={activeThreadId} />
      </div>

      <div className="border-t border-[var(--ui-border)] p-3">
        <UserMenu user={user} initials={initials} />
      </div>
    </aside>
  );
}

function ThreadGroup({
  label,
  threads,
  activeThreadId,
}: {
  label: string;
  threads: AgentThread[];
  activeThreadId?: string;
}) {
  if (threads.length === 0) return null;

  return (
    <div className="mb-3">
      <div className="px-2 py-1 text-[10px] font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase">
        {label}
      </div>
      {threads.map((thread) => (
        <ThreadRow key={thread.id} thread={thread} isActive={thread.id === activeThreadId} />
      ))}
    </div>
  );
}

function ThreadRow({ thread, isActive }: { thread: AgentThread; isActive: boolean }) {
  const badge =
    thread.diffStats && thread.diffStats.additions > 0
      ? `+${thread.diffStats.additions}`
      : null;

  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId: thread.id }}
      className={cn(
        "group mb-0.5 flex items-center gap-2 rounded-lg px-2.5 py-1.5 transition-colors",
        isActive
          ? "bg-[var(--ui-accent-bubble)] text-[var(--ui-text)]"
          : "text-[var(--ui-text-muted)] hover:bg-[var(--ui-sidebar-hover)]",
      )}
    >
      <span
        className={cn(
          "size-2 shrink-0 rounded-full",
          thread.status === "running"
            ? "animate-pulse bg-[var(--ui-accent)]"
            : thread.status === "finished"
              ? "bg-[var(--ui-accent)]"
              : "bg-[var(--ui-border)]",
        )}
      />
      <span className="min-w-0 flex-1 truncate text-xs">{thread.title}</span>
      {badge && (
        <span className="shrink-0 rounded bg-[var(--ui-panel-2)] px-1.5 py-0.5 text-[10px] text-[var(--ui-text-dim)]">
          {badge}
        </span>
      )}
    </Link>
  );
}

function UserMenu({ user, initials }: { user: SessionUser; initials: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2.5 rounded-md px-1 py-1 text-left hover:bg-[var(--ui-sidebar-hover)]"
      >
        <Avatar className="size-7">
          {user.avatar_url && <AvatarImage src={user.avatar_url} alt={user.login} />}
          <AvatarFallback className="text-[10px]">{initials}</AvatarFallback>
        </Avatar>
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium">{user.login}</div>
          <div className="truncate text-[10px] text-[var(--ui-text-dim)]">Team</div>
        </div>
        <GearIcon className="size-4 shrink-0 text-[var(--ui-text-dim)]" />
      </button>
      {open && (
        <div className="absolute right-0 bottom-full left-0 mb-2 overflow-hidden rounded-md border border-[var(--ui-border)] bg-[var(--ui-surface)] p-1 shadow-md">
          <Link
            to="/my-settings"
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs hover:bg-[var(--ui-panel-2)]"
            onClick={() => setOpen(false)}
          >
            Dashboard settings
          </Link>
        </div>
      )}
    </div>
  );
}

export function AgentsShell({
  user,
  activeThreadId,
  children,
}: {
  user: SessionUser;
  activeThreadId?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="agents-ui flex h-svh overflow-hidden bg-[var(--ui-bg)]">
      <AgentsSidebar user={user} activeThreadId={activeThreadId} />
      <div className="flex min-w-0 flex-1">{children}</div>
    </div>
  );
}
