import { useState } from "react"
import { useInfiniteQuery } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { Input } from "@langchain/macaw-components/Input"
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise"

import { api, type AuditLog, type AuditLogFilters } from "@/lib/api"

const DAY = 24 * 60 * 60 * 1000
const FILTERS = [
  ["operation_name", "Operation", "e.g. save_user_settings"],
  ["user_id", "User ID", "Exact UUID"],
  ["api_key_id", "API key ID", "Exact key ID"],
  ["workspace_id", "Workspace ID", "Exact UUID"],
] as const
const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

function localTime(date: Date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 19)
}

function recentRange() {
  const end = new Date()
  return {
    start_time: localTime(new Date(end.getTime() - DAY)),
    end_time: localTime(end),
  }
}

function toFilters(draft: AuditLogFilters): AuditLogFilters {
  return {
    ...Object.fromEntries(
      FILTERS.flatMap(([key]) => {
        const value = draft[key]?.trim()
        return value ? [[key, value]] : []
      })
    ),
    start_time: new Date(draft.start_time).toISOString(),
    end_time: new Date(draft.end_time).toISOString(),
  }
}

function Outcome({ value }: { value: boolean | null }) {
  return (
    <Badge
      size="xs"
      color={
        value === true ? "success" : value === false ? "error" : "secondary"
      }
    >
      {value === true ? "Succeeded" : value === false ? "Failed" : "Unknown"}
    </Badge>
  )
}

function actor(log: AuditLog) {
  const kind = log.enrichments.actor_kind
  if (kind === "api_key") return log.api_key_id ?? "API key"
  if (kind === "agent") return "Agent"
  if (kind === "github_actions") return "GitHub Actions"
  return log.enrichments.actor_login ?? log.user_id ?? "Unknown actor"
}

function AuditDetails({ log }: { log: AuditLog }) {
  const meta = log.enrichments
  const fields: [string, string | number | null][] = [
    ["Event ID", log.id],
    ["Time (UTC)", log.request_time],
    ["Actor type", meta.actor_kind],
    ["Actor / initiating login", meta.actor_login],
    ["User ID", log.user_id],
    ["API key ID", log.api_key_id],
    ["Target workspace ID", log.workspace_id],
    ["Execution workspace", meta.workspace],
    ["Source", meta.source],
    ["HTTP method", meta.request_method],
    ["Route template", meta.request_path],
    ["HTTP status", meta.response_status_code],
    ["Thread ID", meta.thread_id],
    ["Delegated sandbox ID", meta.delegated_from_sandbox_id],
    ["Resource IDs", meta.resource_ids.join(", ") || null],
    ["Settings scope", meta.settings_scope],
  ]
  return (
    <div className="space-y-6">
      <Outcome value={log.operation_succeeded} />
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-5 gap-y-3 text-xs">
        {fields
          .filter(([, value]) => value != null)
          .map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-secondary">{label}</dt>
              <dd className="font-mono break-all text-primary">{value}</dd>
            </div>
          ))}
      </dl>
      {meta.settings_changes != null && (
        <section className="space-y-3">
          <h3 className="text-sm font-medium text-primary">Settings changes</h3>
          <p className="text-xs text-secondary">
            Stored overrides, not effective values. Unset inherits defaults;
            redacted values are not available.
          </p>
          {Object.keys(meta.settings_changes).length === 0 ? (
            <p className="text-xs text-primary">No settings changed.</p>
          ) : (
            <div className="overflow-x-auto rounded-md border border-default">
              <table className="w-full text-left text-xs">
                <thead className="bg-surface-level-2 text-secondary">
                  <tr>
                    <th className="p-space-2 font-medium">Setting</th>
                    <th className="p-space-2 font-medium">Before</th>
                    <th className="p-space-2 font-medium">After</th>
                  </tr>
                </thead>
                <tbody className="text-primary">
                  {Object.entries(meta.settings_changes).map(
                    ([key, change]) => (
                      <tr key={key} className="border-t border-subtle">
                        <td className="p-space-2 font-mono break-all">{key}</td>
                        <td className="p-space-2">
                          {change.before === null
                            ? "Unset"
                            : String(change.before)}
                        </td>
                        <td className="p-space-2">
                          {change.after === null
                            ? "Unset"
                            : String(change.after)}
                        </td>
                      </tr>
                    )
                  )}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  )
}

export function AuditLogs() {
  const [draft, setDraft] = useState<AuditLogFilters>(recentRange)
  const [filters, setFilters] = useState(() => toFilters(draft))
  const [validation, setValidation] = useState("")
  const [selected, setSelected] = useState<AuditLog | null>(null)
  const logs = useInfiniteQuery({
    queryKey: ["auditLogs", filters],
    queryFn: ({ pageParam }) => api.listAuditLogs(filters, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.cursor ?? undefined,
    refetchOnWindowFocus: false,
    retry: false,
  })
  const items = logs.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <div className="space-y-6">
      <form
        className="space-y-4 rounded-lg border border-default bg-surface-level-1 p-space-4"
        onSubmit={(event) => {
          event.preventDefault()
          const start = new Date(draft.start_time).getTime()
          const end = new Date(draft.end_time).getTime()
          if (
            !Number.isFinite(start) ||
            !Number.isFinite(end) ||
            start > end ||
            end - start > 31 * DAY
          ) {
            setValidation("Choose an ordered time range of at most 31 days.")
            return
          }
          setValidation("")
          setFilters(toFilters(draft))
        }}
      >
        <div className="flex flex-wrap items-end gap-space-3">
          {(["start_time", "end_time"] as const).map((key) => (
            <label
              key={key}
              className="min-w-0 basis-full space-y-1.5 text-xs font-medium text-primary sm:flex-1 sm:basis-auto"
            >
              <span>{key === "start_time" ? "From" : "To"} (local time)</span>
              <Input
                type="datetime-local"
                size="md"
                step="1"
                required
                value={draft[key]}
                onChange={(value) => setDraft({ ...draft, [key]: value })}
              />
            </label>
          ))}
          <Button
            type="button"
            size="md"
            color="secondary"
            variant="outlined"
            onClick={() => setDraft({ ...draft, ...recentRange() })}
          >
            Last 24 hours
          </Button>
        </div>
        <div className="grid gap-space-3 sm:grid-cols-2 lg:grid-cols-4">
          {FILTERS.map(([key, label, placeholder]) => (
            <label
              key={key}
              className="space-y-1.5 text-xs font-medium text-primary"
            >
              <span>{label}</span>
              <Input
                size="md"
                value={draft[key] ?? ""}
                placeholder={placeholder}
                maxLength={key === "operation_name" ? 128 : undefined}
                pattern={
                  key === "user_id" || key === "workspace_id"
                    ? UUID_PATTERN
                    : undefined
                }
                onChange={(value) => setDraft({ ...draft, [key]: value })}
              />
            </label>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-space-3">
          <p className="text-xs text-secondary">
            Filters match exactly. Maximum range: 31 days.
          </p>
          <Button type="submit" size="md" color="primary">
            Apply filters
          </Button>
        </div>
        {validation && (
          <p role="alert" className="text-xs text-error-secondary">
            {validation}
          </p>
        )}
      </form>

      <section
        className="space-y-3"
        aria-label="Audit events"
        aria-busy={logs.isFetching}
      >
        <div className="flex items-center justify-between gap-space-3">
          <div className="space-y-1">
            <h2 className="text-sm font-medium text-primary">Events</h2>
            <p className="text-xs text-secondary">
              {items.length} loaded · Newest first · Times shown locally
            </p>
          </div>
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            leftDecorator={ArrowClockwiseIcon}
            disabled={logs.isFetching}
            onClick={() => void logs.refetch()}
          >
            Reload
          </Button>
        </div>
        {logs.isError && (
          <div role="alert">
            <Banner
              intent="error"
              title="Could not load audit logs."
              action={
                <Button
                  size="xs"
                  color="secondary"
                  variant="outlined"
                  disabled={logs.isFetching}
                  onClick={() =>
                    void (logs.isFetchNextPageError
                      ? logs.fetchNextPage()
                      : logs.refetch())
                  }
                >
                  Try again
                </Button>
              }
            >
              {logs.error.message}
            </Banner>
          </div>
        )}
        <div className="overflow-x-auto rounded-lg border border-default bg-surface-level-1">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="border-b border-default bg-surface-level-2 text-secondary">
              <tr>
                {["Time", "Operation", "Actor", "Outcome", "Details"].map(
                  (heading) => (
                    <th
                      key={heading}
                      scope="col"
                      className="px-space-4 py-space-3 font-medium"
                    >
                      {heading}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-subtle text-primary">
              {items.map((log) => (
                <tr key={log.id} className="hover:bg-surface-level-1-hover">
                  <td className="px-space-4 py-space-4 whitespace-nowrap">
                    <time dateTime={log.request_time}>
                      {new Date(log.request_time).toLocaleString()}
                    </time>
                  </td>
                  <td className="px-space-4 py-space-4">
                    <div className="font-mono break-all">
                      {log.operation_name}
                    </div>
                    <div className="mt-1 text-secondary">
                      {log.enrichments.source === "tool"
                        ? "Agent tool"
                        : "HTTP API"}
                    </div>
                  </td>
                  <td className="max-w-48 px-space-4 py-space-4 break-all">
                    {actor(log)}
                  </td>
                  <td className="px-space-4 py-space-4">
                    <Outcome value={log.operation_succeeded} />
                  </td>
                  <td className="px-space-4 py-space-4">
                    <Button
                      size="xs"
                      color="secondary"
                      variant="plain"
                      aria-label={`View ${log.operation_name} event`}
                      onClick={() => setSelected(log)}
                    >
                      View
                    </Button>
                  </td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-space-4 py-12 text-center text-secondary"
                  >
                    {logs.isPending
                      ? "Loading audit logs…"
                      : logs.isError
                        ? "Audit logs unavailable."
                        : "No events match this time range and filters."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {logs.hasNextPage && (
          <div className="flex justify-center">
            <Button
              size="md"
              color="secondary"
              variant="outlined"
              disabled={logs.isFetching}
              onClick={() => void logs.fetchNextPage()}
            >
              {logs.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}
      </section>
      <p className="text-xs/relaxed text-secondary">
        Recording is best effort and covers authenticated API writes and
        selected agent tools, not all activity. HTTP success reflects the
        response status, not proof of a committed change.
      </p>
      <Dialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
      >
        <DialogContent
          title={<span className="break-all">{selected?.operation_name}</span>}
          description="Recorded event metadata"
          className="w-[42rem] max-w-[calc(100vw-2rem)]"
        >
          {selected && <AuditDetails log={selected} />}
        </DialogContent>
      </Dialog>
    </div>
  )
}
