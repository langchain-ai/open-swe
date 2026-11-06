import { Button } from "@langchain/macaw-components/Button"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { CaretRightIcon } from "@langchain/macaw-components/icons"
import { PulseIcon } from "@phosphor-icons/react/dist/ssr/Pulse"
import { TerminalWindowIcon } from "@phosphor-icons/react/dist/ssr/TerminalWindow"
import { useEffect, useRef, useState } from "react"
import type { ComponentType, ReactNode } from "react"

import {
  useAgentThreadBackgroundOutput,
  useAgentThreadBackgroundWork,
} from "@/features/agents/lib/queries"
import type {
  AgentBackgroundCommand,
  AgentThreadMonitor,
} from "@/features/agents/lib/types"
import { cn, formatRelativeTime } from "@/lib/utils"

/** Whether the thread has a command or monitor running, so the panel is worth opening. */
export function useHasBackgroundWork(threadId: string): boolean {
  const work = useAgentThreadBackgroundWork(threadId).data
  return Boolean(work?.monitors.length || work?.commands.length)
}

/**
 * The thread's running sandbox monitors and background commands, as a right
 * panel surface. Each row opens to its full command and latest output.
 */
export function BackgroundWorkPanel({ threadId }: { threadId: string }) {
  const work = useAgentThreadBackgroundWork(threadId).data
  const monitors = work?.monitors ?? []
  const commands = work?.commands ?? []
  if (monitors.length === 0 && commands.length === 0) {
    return (
      <p className="flex flex-1 items-center justify-center px-space-5 text-center text-xs text-secondary">
        Nothing is running in the background.
      </p>
    )
  }
  return (
    <div
      data-testid="background-work-panel"
      className="flex min-h-0 flex-1 flex-col overflow-y-auto"
    >
      {monitors.length > 0 && (
        <Section title="Monitors" icon={PulseIcon}>
          {monitors.map((monitor) => (
            <MonitorRow
              key={monitor.monitor_id}
              threadId={threadId}
              monitor={monitor}
            />
          ))}
        </Section>
      )}
      {commands.length > 0 && (
        <Section title="Commands" icon={TerminalWindowIcon}>
          {commands.map((command) => (
            <CommandRow
              key={command.task_id}
              threadId={threadId}
              command={command}
            />
          ))}
        </Section>
      )}
    </div>
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
  threadId,
  taskId,
  title,
  command,
  summary,
  details,
}: {
  threadId: string
  taskId: string
  title: string
  command: string | null
  summary: string
  /** Shown once the row is open, above the output. */
  details: ReadonlyArray<[label: string, value: string]>
}) {
  const [open, setOpen] = useState(false)
  return (
    <li className="flex flex-col">
      <Button
        color="secondary"
        variant="plain"
        size="sm"
        aria-label={title}
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="h-auto w-full cursor-pointer justify-start gap-space-2 px-space-3 py-space-2 text-left whitespace-normal hover:bg-surface-level-1-hover"
      >
        <Spinner size="xs" className="mt-0.5 shrink-0 text-icon-brand" />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span
            className={cn(
              "text-sm text-primary",
              open ? "break-all" : "truncate"
            )}
          >
            {title}
          </span>
          {command && command !== title && !open && (
            <code className="truncate font-mono text-xs text-secondary">
              {command}
            </code>
          )}
          <span className="text-xs text-tertiary">{summary}</span>
        </span>
        <CaretRightIcon
          className={cn(
            "mt-space-1 size-3 shrink-0 text-icon-secondary transition-transform",
            open && "rotate-90"
          )}
        />
      </Button>
      {open && (
        <div className="flex gap-space-2 px-space-3 pb-space-3">
          {/* Lines the details up under the title. */}
          <Spinner size="xs" className="invisible shrink-0" aria-hidden />
          <div className="flex min-w-0 flex-1 flex-col gap-space-2">
            {command && command !== title && (
              <code className="font-mono text-xs break-all whitespace-pre-wrap text-secondary">
                {command}
              </code>
            )}
            {details.length > 0 && (
              <dl className="grid grid-cols-[auto_1fr] gap-x-space-3 gap-y-0.5 text-xs">
                {details.map(([label, value]) => (
                  <div key={label} className="contents">
                    <dt className="text-tertiary">{label}</dt>
                    <dd className="min-w-0 font-mono break-all text-secondary">
                      {value}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
            <Output threadId={threadId} taskId={taskId} />
          </div>
        </div>
      )}
    </li>
  )
}

function Output({ threadId, taskId }: { threadId: string; taskId: string }) {
  const output = useAgentThreadBackgroundOutput(threadId, taskId, true)
  const text = output.data?.output
  // Follow the newest lines, as a terminal does.
  const ref = useRef<HTMLPreElement>(null)
  useEffect(() => {
    const node = ref.current
    if (node) node.scrollTop = node.scrollHeight
  }, [text])
  const empty = output.isPending
    ? "Loading output…"
    : output.error
      ? "Couldn't load the output."
      : "No output yet."
  return (
    <pre
      ref={ref}
      aria-label="Latest output"
      className="max-h-72 overflow-auto rounded-md border border-default bg-surface-level-1 p-space-2 font-mono text-xs leading-relaxed whitespace-pre-wrap text-secondary"
    >
      {text || empty}
    </pre>
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

function MonitorRow({
  threadId,
  monitor,
}: {
  threadId: string
  monitor: AgentThreadMonitor
}) {
  const summary = [
    started(monitor.started_at),
    plural(monitor.events, "event"),
    plural(monitor.raw_lines, "line"),
  ]
    .filter(Boolean)
    .join(" · ")
  const details: Array<[string, string]> = []
  if (monitor.filter) details.push(["Filter", monitor.filter])
  if (monitor.started_at !== null && monitor.timeout !== null) {
    details.push([
      "Stops",
      formatRelativeTime((monitor.started_at + monitor.timeout) * 1000),
    ])
  }
  return (
    <Row
      threadId={threadId}
      taskId={monitor.monitor_id}
      title={monitor.description || monitor.command}
      command={monitor.command}
      summary={summary}
      details={details}
    />
  )
}

function CommandRow({
  threadId,
  command,
}: {
  threadId: string
  command: AgentBackgroundCommand
}) {
  return (
    <Row
      threadId={threadId}
      taskId={command.task_id}
      title={command.command ?? command.task_id}
      command={command.command}
      summary={started(command.started_at) ?? "Running"}
      details={[]}
    />
  )
}
