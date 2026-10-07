import { useEffect } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { LoaderCircle, Users } from "lucide-react"

import { agentThreadKeys } from "@/features/agents/lib/queries"
import type { AgentThread, Message } from "@/features/agents/lib/types"

export function TaskWorkersStatus({
  thread,
  messages,
  unavailable = false,
}: {
  thread: AgentThread
  messages: Array<Message>
  unavailable?: boolean
}) {
  const queryClient = useQueryClient()
  const workers = thread.taskWorkers ?? []
  const spawns = messages.flatMap((message) =>
    message.chunks.flatMap((chunk) =>
      chunk.kind === "tool-execution" && chunk.toolName === "spawn_worker"
        ? [chunk]
        : []
    )
  )
  const spawnState = spawns
    .map((spawn) => `${spawn.toolCallId}:${spawn.status}`)
    .join(",")
  useEffect(() => {
    if (spawnState)
      void queryClient.invalidateQueries({
        queryKey: agentThreadKeys.detail(thread.id),
      })
  }, [queryClient, thread.id, spawnState])

  const starting = spawns.some(
    (spawn) => spawn.status === "pending" || spawn.status === "in_progress"
  )
  if (!workers.length && !spawns.length) return null

  const statuses = workers.map((worker) => {
    if (unavailable) return "status unavailable"
    switch (worker.status) {
      case "running":
        return "running"
      case "finished":
        return "completed"
      case "error":
        return "failed"
      case "interrupted":
        return "cancelled / interrupted"
      case "idle":
        return "idle"
      default:
        return "status unknown"
    }
  })
  const counts = [...new Set(statuses)].map(
    (status) =>
      `${statuses.filter((value) => value === status).length} ${status}`
  )
  const active = starting || statuses.includes("running")
  const label = counts.length
    ? counts.join(" · ")
    : starting
      ? "Starting worker…"
      : spawns.some((spawn) => spawn.status === "error")
        ? "Worker launch failed"
        : "Worker status unavailable"

  return (
    <div className="shrink-0 border-b px-4 py-2 text-xs">
      <details className="mx-auto max-w-3xl">
        <summary className="flex cursor-pointer list-none items-center gap-2 rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
          {active ? (
            <LoaderCircle aria-hidden className="size-3.5 animate-spin" />
          ) : (
            <Users aria-hidden className="size-3.5" />
          )}
          <span className="font-medium text-foreground">Workers</span>
          <span role="status" aria-live="polite">
            {label}
            {counts.length > 0 && starting ? " · starting worker…" : ""}
          </span>
        </summary>
        <ul className="mt-2 space-y-1.5 pl-5">
          {workers.map((worker, index) => (
            <li key={worker.id}>
              <Link
                to="/agents/$threadId"
                params={{ threadId: worker.id }}
                className="flex items-center justify-between gap-3 rounded-sm hover:underline"
              >
                <span className="truncate">{worker.title}</span>
                <span className="shrink-0 text-muted-foreground">
                  {statuses[index]}
                </span>
              </Link>
            </li>
          ))}
          {!workers.length && (
            <li className="text-muted-foreground">
              {starting
                ? "Waiting for the worker to start."
                : "No live worker status is available yet."}
            </li>
          )}
        </ul>
      </details>
    </div>
  )
}
