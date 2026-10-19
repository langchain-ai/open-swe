import { memo } from "react"
import { useQuery } from "@tanstack/react-query"

import { SlackBlocks, SlackIcon } from "./ReplyCard"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { api } from "@/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"

function slackletId(output: string | undefined): string | null {
  if (!output) return null
  try {
    const parsed: unknown = JSON.parse(output)
    const id =
      parsed && typeof parsed === "object"
        ? (parsed as { slacklet_id?: unknown }).slacklet_id
        : undefined
    return typeof id === "string" ? id : null
  } catch {
    return null
  }
}

/**
 * The live slacklet a `start_slacklet` call started, of any kind: the message
 * exactly as Slack shows it, then the immutable commands that built it.
 */
export const SlackletCard = memo(function SlackletCard({
  chunk,
}: {
  chunk: ToolExecutionChunk
}) {
  const id = slackletId(chunk.output)
  const { data, error } = useQuery({
    queryKey: ["slacklet", id],
    queryFn: () => api.slacklet(id ?? ""),
    enabled: id !== null,
    meta: {
      invalidatedBy: id ? [invalidationTopic("slacklets", id)] : [],
    },
  })
  if (chunk.status === "pending" || chunk.status === "in_progress") {
    return (
      <div className="px-space-1 text-xxs text-secondary">
        Starting slacklet…
      </div>
    )
  }
  if (id === null) {
    return (
      <div className="px-space-1 text-xxs text-error-secondary">
        Could not start the slacklet
      </div>
    )
  }
  return (
    <section
      data-testid="slacklet-card"
      className="min-w-0 rounded-lg border border-default bg-surface-level-1 p-space-3"
    >
      <div className="mb-space-2 flex items-center gap-space-2 text-xxs text-secondary">
        <SlackIcon />
        <span>Live in Slack</span>
      </div>
      {error ? (
        <p className="text-xs text-error-secondary">
          Could not load the slacklet
        </p>
      ) : data ? (
        <>
          <div
            data-testid="slacklet-message"
            className="border-brand-primary border-l-2 pl-space-3 text-sm"
          >
            <SlackBlocks blocks={data.blocks} />
          </div>
          <details data-testid="slacklet-log" className="mt-space-2">
            <summary className="w-fit cursor-pointer text-xxs text-tertiary hover:text-secondary">
              {data.commands.length}{" "}
              {data.commands.length === 1 ? "command" : "commands"}
            </summary>
            <ol
              data-testid="slacklet-commands"
              className="mt-space-1 flex flex-col gap-0.5 font-mono text-xxs text-secondary"
            >
              {data.commands.map((command) => (
                <li
                  key={command.seq}
                  className="flex gap-space-2"
                  title={JSON.stringify(command.command)}
                >
                  <span className="shrink-0">
                    #{command.seq} {command.op}
                  </span>
                  <span className="min-w-0 [overflow-wrap:anywhere]">
                    {command.summary}
                  </span>
                </li>
              ))}
            </ol>
          </details>
        </>
      ) : (
        <p className="text-xs text-secondary">Loading slacklet…</p>
      )}
    </section>
  )
})
