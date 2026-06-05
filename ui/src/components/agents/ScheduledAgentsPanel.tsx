import { CalendarClock, Pause, Play, Trash2 } from "lucide-react"
import { useMemo, useState } from "react"
import { Link } from "@tanstack/react-router"

import type { ModelSelection } from "@/lib/agents/useModelOptions"
import { RepoSelector } from "@/components/agents/RepoSelector"
import {
  useAgentSchedules,
  useCreateAgentSchedule,
  useDeleteAgentSchedule,
  useUpdateAgentSchedule,
} from "@/lib/agents/queries"

interface ScheduledAgentsPanelProps {
  repos?: Array<{ full_name: string }>
  defaultRepo?: string | null
  selection?: ModelSelection | null
}

const EXAMPLE_CRONS = [
  { label: "Weekdays at 9 AM UTC", value: "0 9 * * 1-5" },
  { label: "Daily at 2 AM UTC", value: "0 2 * * *" },
  { label: "Every 2 hours", value: "0 */2 * * *" },
]

function formatDate(value?: string | null): string {
  if (!value) return "Never run"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return "Never run"
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

export function ScheduledAgentsPanel({
  repos,
  defaultRepo,
  selection,
}: ScheduledAgentsPanelProps) {
  const schedulesQuery = useAgentSchedules()
  const createSchedule = useCreateAgentSchedule()
  const updateSchedule = useUpdateAgentSchedule()
  const deleteSchedule = useDeleteAgentSchedule()
  const [name, setName] = useState("")
  const [prompt, setPrompt] = useState("")
  const [schedule, setSchedule] = useState("0 9 * * 1-5")
  const [repo, setRepo] = useState<string | null>(defaultRepo ?? null)

  const schedules = schedulesQuery.data ?? []
  const isPending = createSchedule.isPending
  const canSubmit =
    prompt.trim().length > 0 && schedule.trim().length > 0 && !isPending
  const errorMessage = useMemo(() => {
    const error =
      createSchedule.error || updateSchedule.error || deleteSchedule.error
    return error instanceof Error ? error.message : null
  }, [createSchedule.error, deleteSchedule.error, updateSchedule.error])

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    createSchedule.mutate(
      {
        name: name.trim() || null,
        prompt: prompt.trim(),
        schedule: schedule.trim(),
        repo,
        model_id: selection?.modelId ?? null,
        effort: selection?.effort ?? null,
      },
      {
        onSuccess: () => {
          setName("")
          setPrompt("")
        },
      }
    )
  }

  return (
    <section className="mt-8 w-full max-w-2xl rounded-2xl border border-[var(--ui-border)] bg-[var(--ui-surface)] p-4 shadow-sm">
      <div className="mb-4 flex items-start gap-3">
        <div className="rounded-lg bg-[var(--ui-panel-2)] p-2 text-[var(--ui-text)]">
          <CalendarClock className="size-4" />
        </div>
        <div>
          <h2 className="text-sm font-semibold text-[var(--ui-text)]">
            Scheduled agents
          </h2>
          <p className="mt-1 text-xs text-[var(--ui-text-dim)]">
            Run Open SWE on a recurring cron schedule. Times are interpreted by
            LangGraph cron.
          </p>
        </div>
      </div>

      <form onSubmit={onSubmit} className="space-y-3">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Schedule name"
          className="w-full rounded-lg border border-[var(--ui-border)] bg-[var(--ui-bg)] px-3 py-2 text-sm text-[var(--ui-text)] outline-none placeholder:text-[var(--ui-text-dim)]"
        />
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="What should Open SWE do each time this runs?"
          rows={3}
          className="w-full resize-none rounded-lg border border-[var(--ui-border)] bg-[var(--ui-bg)] px-3 py-2 text-sm text-[var(--ui-text)] outline-none placeholder:text-[var(--ui-text-dim)]"
        />
        <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <div>
            <input
              value={schedule}
              onChange={(e) => setSchedule(e.target.value)}
              placeholder="0 9 * * 1-5"
              className="w-full rounded-lg border border-[var(--ui-border)] bg-[var(--ui-bg)] px-3 py-2 font-mono text-sm text-[var(--ui-text)] outline-none placeholder:text-[var(--ui-text-dim)]"
            />
            <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-[var(--ui-text-dim)]">
              {EXAMPLE_CRONS.map((example) => (
                <button
                  key={example.value}
                  type="button"
                  onClick={() => setSchedule(example.value)}
                  className="rounded bg-[var(--ui-panel-2)] px-2 py-0.5 hover:text-[var(--ui-text)]"
                >
                  {example.label}
                </button>
              ))}
            </div>
          </div>
          <button
            type="submit"
            disabled={!canSubmit}
            className="h-10 rounded-lg bg-[var(--ui-accent)] px-4 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-default disabled:opacity-40"
          >
            {isPending ? "Scheduling..." : "Create schedule"}
          </button>
        </div>
        <RepoSelector
          repos={repos}
          selectedRepo={repo}
          onRepoChange={setRepo}
        />
        {errorMessage && <p className="text-xs text-red-400">{errorMessage}</p>}
      </form>

      {schedules.length > 0 && (
        <div className="mt-5 space-y-2">
          {schedules.map((item) => {
            const isUpdating =
              updateSchedule.isPending &&
              updateSchedule.variables.scheduleId === item.id
            const isDeleting =
              deleteSchedule.isPending && deleteSchedule.variables === item.id
            return (
              <div
                key={item.id}
                className="flex items-start gap-3 rounded-xl border border-[var(--ui-border)] bg-[var(--ui-bg)] px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-medium text-[var(--ui-text)]">
                      {item.name}
                    </span>
                    <span className="rounded bg-[var(--ui-panel-2)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--ui-text-dim)]">
                      {item.schedule}
                    </span>
                    {!item.enabled && (
                      <span className="rounded bg-[var(--ui-panel-2)] px-1.5 py-0.5 text-[10px] text-[var(--ui-text-dim)]">
                        paused
                      </span>
                    )}
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs text-[var(--ui-text-muted)]">
                    {item.prompt}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[var(--ui-text-dim)]">
                    {item.repo && <span>{item.repo}</span>}
                    <span>Last run: {formatDate(item.lastTriggeredAt)}</span>
                    {item.lastThreadId && (
                      <Link
                        to="/agents/$threadId"
                        params={{ threadId: item.lastThreadId }}
                        className="text-[var(--ui-accent)] hover:underline"
                      >
                        View latest run
                      </Link>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    disabled={isUpdating}
                    onClick={() =>
                      updateSchedule.mutate({
                        scheduleId: item.id,
                        enabled: !item.enabled,
                      })
                    }
                    className="rounded p-1.5 text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)] disabled:opacity-40"
                    aria-label={
                      item.enabled ? "Pause schedule" : "Resume schedule"
                    }
                  >
                    {item.enabled ? (
                      <Pause className="size-3.5" />
                    ) : (
                      <Play className="size-3.5" />
                    )}
                  </button>
                  <button
                    type="button"
                    disabled={isDeleting}
                    onClick={() => {
                      if (window.confirm(`Delete "${item.name}"?`))
                        deleteSchedule.mutate(item.id)
                    }}
                    className="rounded p-1.5 text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)] disabled:opacity-40"
                    aria-label="Delete schedule"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
