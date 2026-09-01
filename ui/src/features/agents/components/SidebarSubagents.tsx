import { Link } from "@tanstack/react-router"
import {
  CaretRightIcon,
  CheckCircleIcon,
  CircleNotchIcon,
  RobotIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"

import {
  AgentThreadStreamProvider,
  useAgentThreadRuntime,
} from "@/features/agents/lib/AgentThreadStreamProvider"

export function SidebarSubagents({ threadId }: { threadId: string }) {
  return (
    <AgentThreadStreamProvider threadId={threadId}>
      <SubagentRows threadId={threadId} />
    </AgentThreadStreamProvider>
  )
}

function SubagentRows({ threadId }: { threadId: string }) {
  const stream = useAgentThreadRuntime()
  const subagents = [...stream.subagents.values()].sort(
    (left, right) => left.startedAt.getTime() - right.startedAt.getTime()
  )

  if (stream.isThreadLoading) {
    return (
      <div className="flex h-7 items-center gap-2 pl-8 text-[11px] text-muted-foreground/70">
        <CircleNotchIcon className="size-3 animate-spin" />
        Loading subagents…
      </div>
    )
  }
  if (subagents.length === 0) {
    return (
      <div className="h-7 pl-8 text-[11px] leading-7 text-muted-foreground/60">
        No subagents
      </div>
    )
  }

  return subagents.map((subagent) => {
    const Icon =
      subagent.status === "running"
        ? CircleNotchIcon
        : subagent.status === "error"
          ? WarningCircleIcon
          : CheckCircleIcon
    const label = subagent.taskInput || subagent.name

    return (
      <Link
        key={subagent.id}
        to="/agents/$threadId"
        params={{ threadId }}
        hash={`subagent-${subagent.id}`}
        className="group/subagent mb-0.5 flex h-7 min-w-0 items-center gap-1.5 rounded-lg pr-2.5 text-[12px] text-muted-foreground transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
        style={{ paddingLeft: `${32 + subagent.depth * 12}px` }}
        title={label}
      >
        <CaretRightIcon className="size-3 shrink-0 opacity-50" />
        <RobotIcon className="size-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <Icon
          className={`size-3 shrink-0 ${subagent.status === "running" ? "animate-spin" : ""}`}
          aria-label={`Subagent ${subagent.status}`}
        />
      </Link>
    )
  })
}
