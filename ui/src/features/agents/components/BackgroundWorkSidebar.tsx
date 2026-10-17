import { Spinner } from "@langchain/macaw-components/Spinner"
import { PulseIcon } from "@phosphor-icons/react/dist/ssr/Pulse"
import { TerminalWindowIcon } from "@phosphor-icons/react/dist/ssr/TerminalWindow"
import type { ComponentType, ReactNode } from "react"

import { useAgentThreadBackgroundWork } from "@/features/agents/lib/queries"
import type {
  AgentBackgroundCommand,
  AgentThreadMonitor,
} from "@/features/agents/lib/types"
import { formatRelativeTime } from "@/lib/utils"

/**
 * The thread's running sandbox monitors and background commands, beside the
 * chat. Shown only while something runs, and only where the chat keeps its width.
 */
export function BackgroundWorkSidebar({ threadId }: { threadId: string }) {
  const work = useAgentThreadBackgroundWork(threadId).data
  const monitors = work?.monitors ?? []
  const commands = work?.commands ?? []
  if (monitors.length === 0 && commands.length === 0) return null
  return (
    <aside
      aria-label="Background work"
      data-testid="background-work-sidebar"
      className="hidden w-72 shrink-0 flex-col overflow-y-auto border-l border-default bg-surface-level-2 min-[1100px]:flex"
    >
      <div className="flex h-11 shrink-0 items-center border-b border-default px-space-3 pr-space-8">
        <h2 className="text-sm font-medium text-primary">Background Work</h2>
      </div>
      {monitors.length > 0 && (
        <Section title="Monitors" icon={PulseIcon}>
          {monitors.map((monitor) => (
            <MonitorRow key={monitor.monitor_id} monitor={monitor} />
          ))}
        </Section>
      )}
      {commands.length > 0 && (
        <Section title="Commands" icon={TerminalWindowIcon}>
          {commands.map((command) => (
            <CommandRow key={command.task_id} command={command} />
          ))}
        </Section>
      )}
    </aside>
  )
}

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string
  icon: ComponentType<{ className?: string }>
  children: ReactNode
}) {
  return (
    <section aria-label={title} className="flex flex-col py-space-2">
      <h3 className="flex items-center gap-space-2 px-space-3 py-space-1 text-xs font-medium text-secondary">
        <Icon className="size-3.5" />
        {title}
      </h3>
      <ul className="flex flex-col">{children}</ul>
    </section>
  )
}

function Row({
  title,
  command,
  detail,
}: {
  title: string
  command: string | null
  detail: string
}) {
  return (
    <li className="flex gap-space-2 px-space-3 py-space-2">
      <Spinner size="xs" className="mt-0.5 shrink-0 text-icon-brand" />
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate text-sm text-primary" title={title}>
          {title}
        </span>
        {command && command !== title && (
          <code
            className="truncate font-mono text-xs text-secondary"
            title={command}
          >
            {command}
          </code>
        )}
        <span className="text-xs text-tertiary">{detail}</span>
      </div>
    </li>
  )
}

function started(startedAt: number | null): string | null {
  return startedAt === null
    ? null
    : `Started ${formatRelativeTime(startedAt * 1000)}`
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`
}

function MonitorRow({ monitor }: { monitor: AgentThreadMonitor }) {
  const detail = [
    started(monitor.started_at),
    plural(monitor.events, "event"),
    plural(monitor.raw_lines, "line"),
  ]
    .filter(Boolean)
    .join(" · ")
  return (
    <Row
      title={monitor.description || monitor.command}
      command={monitor.command}
      detail={detail}
    />
  )
}

function CommandRow({ command }: { command: AgentBackgroundCommand }) {
  return (
    <Row
      title={command.command ?? command.task_id}
      command={null}
      detail={started(command.started_at) ?? "Running"}
    />
  )
}
