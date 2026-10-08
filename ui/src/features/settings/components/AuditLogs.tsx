import { useState } from "react"
import { useInfiniteQuery } from "@tanstack/react-query"
import { ArrowClockwiseIcon } from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogPopup,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  api,
  type AuditLog,
  type AuditLogFilters,
  type ExpeditedExclusions,
} from "@/lib/api"

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
    <Badge variant={value === false ? "destructive" : "secondary"}>
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
    <>
      <div className="flex items-start justify-between gap-4 border-b p-5">
        <div className="min-w-0 space-y-2">
          <DialogTitle className="break-all">{log.operation_name}</DialogTitle>
          <DialogDescription>Recorded event metadata</DialogDescription>
        </div>
        <DialogClose render={<Button variant="ghost" size="sm" />}>
          Close
        </DialogClose>
      </div>
      <div className="space-y-6 overflow-y-auto p-5">
        <Outcome value={log.operation_succeeded} />
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-5 gap-y-3 text-xs">
          {fields
            .filter(([, value]) => value != null)
            .map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="font-mono break-all">{value}</dd>
              </div>
            ))}
        </dl>
        {meta.settings_changes != null && (
          <section className="space-y-3">
            <h3 className="text-sm font-medium">Settings changes</h3>
            <p className="text-xs text-muted-foreground">
              Stored overrides, not effective values. Unset inherits defaults;
              redacted values are not available.
            </p>
            {Object.keys(meta.settings_changes).length === 0 ? (
              <p className="text-xs">No settings changed.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-muted-foreground">
                    <tr>
                      <th className="p-2">Setting</th>
                      <th className="p-2">Before</th>
                      <th className="p-2">After</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(meta.settings_changes).map(
                      ([key, change]) => (
                        <tr key={key} className="border-t">
                          <td className="p-2 font-mono break-all">{key}</td>
                          <td className="p-2">
                            {change.before === null
                              ? "Unset"
                              : String(change.before)}
                          </td>
                          <td className="p-2">
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
        {meta.expedited_exclusions != null && (
          <ExclusionsSection exclusions={meta.expedited_exclusions} />
        )}
      </div>
    </>
  )
}

function ExclusionsSection({
  exclusions,
}: {
  exclusions: ExpeditedExclusions
}) {
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium">
        Excluded under .open-swe/APPROVALS.md
      </h3>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-5 gap-y-3 text-xs">
        {(
          [
            ["Pull request ID", exclusions.pull_request_id ?? "Not stored"],
            ["Base SHA", exclusions.base_sha],
            ["Head SHA", exclusions.head_sha],
            ["APPROVALS.md SHA-256", exclusions.approvals_md_sha256],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-mono break-all">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-muted-foreground">
            <tr>
              <th className="p-2">File</th>
              <th className="p-2">Hunk</th>
              <th className="p-2">Lines</th>
              <th className="p-2">Guideline</th>
              <th className="p-2">Reason</th>
            </tr>
          </thead>
          <tbody>
            {exclusions.hunks.length > 0
              ? exclusions.hunks.map((hunk, index) => (
                  <tr
                    key={`${hunk.path}:${hunk.digest}:${index}`}
                    className="border-t"
                  >
                    <td className="p-2 font-mono break-all">{hunk.path}</td>
                    <td className="p-2 font-mono break-all">
                      {hunk.header || "Whole file"}
                    </td>
                    <td className="p-2 whitespace-nowrap">
                      +{hunk.additions} −{hunk.deletions}
                    </td>
                    <td className="p-2">{hunk.guideline}</td>
                    <td className="p-2">{hunk.reason}</td>
                  </tr>
                ))
              : exclusions.requested.map((request, index) => (
                  <tr key={`${request.path}:${index}`} className="border-t">
                    <td className="p-2 font-mono break-all">{request.path}</td>
                    <td className="p-2 font-mono break-all">
                      {request.hunks.length > 0
                        ? `Starting at ${request.hunks.join(", ")}`
                        : "Whole file"}
                    </td>
                    <td className="p-2">Not resolved</td>
                    <td className="p-2">{request.guideline}</td>
                    <td className="p-2">{request.reason}</td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
    </section>
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
        className="space-y-4 rounded-xl border bg-card p-4"
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
        <div className="flex flex-wrap items-end gap-3">
          {(["start_time", "end_time"] as const).map((key) => (
            <label
              key={key}
              className="min-w-0 basis-full space-y-1.5 text-xs font-medium sm:flex-1 sm:basis-auto"
            >
              <span>{key === "start_time" ? "From" : "To"} (local time)</span>
              <Input
                type="datetime-local"
                step="1"
                required
                value={draft[key]}
                onChange={(event) =>
                  setDraft({ ...draft, [key]: event.target.value })
                }
              />
            </label>
          ))}
          <Button
            type="button"
            variant="outline"
            onClick={() => setDraft({ ...draft, ...recentRange() })}
          >
            Last 24 hours
          </Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {FILTERS.map(([key, label, placeholder]) => (
            <label key={key} className="space-y-1.5 text-xs font-medium">
              <span>{label}</span>
              <Input
                value={draft[key] ?? ""}
                placeholder={placeholder}
                maxLength={key === "operation_name" ? 128 : undefined}
                pattern={
                  key === "user_id" || key === "workspace_id"
                    ? UUID_PATTERN
                    : undefined
                }
                onChange={(event) =>
                  setDraft({ ...draft, [key]: event.target.value })
                }
              />
            </label>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            Filters match exactly. Maximum range: 31 days.
          </p>
          <Button type="submit">Apply filters</Button>
        </div>
        {validation && (
          <p role="alert" className="text-xs text-destructive">
            {validation}
          </p>
        )}
      </form>

      <section
        className="space-y-3"
        aria-label="Audit events"
        aria-busy={logs.isFetching}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-sm font-medium">Events</h2>
            <p className="text-xs text-muted-foreground">
              {items.length} loaded · Newest first · Times shown locally
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={logs.isFetching}
            onClick={() => void logs.refetch()}
          >
            <ArrowClockwiseIcon className="size-3.5" /> Reload
          </Button>
        </div>
        {logs.isError && (
          <div
            role="alert"
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/30 p-4 text-xs"
          >
            <p>Could not load audit logs: {logs.error.message}</p>
            <Button
              size="sm"
              variant="outline"
              disabled={logs.isFetching}
              onClick={() =>
                void (logs.isFetchNextPageError
                  ? logs.fetchNextPage()
                  : logs.refetch())
              }
            >
              Try again
            </Button>
          </div>
        )}
        <div className="overflow-x-auto rounded-xl border bg-card">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="border-b bg-muted/40 text-muted-foreground">
              <tr>
                {["Time", "Operation", "Actor", "Outcome", "Details"].map(
                  (heading) => (
                    <th
                      key={heading}
                      scope="col"
                      className="px-4 py-3 font-medium"
                    >
                      {heading}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody className="divide-y">
              {items.map((log) => (
                <tr key={log.id} className="hover:bg-muted/30">
                  <td className="px-4 py-4 whitespace-nowrap">
                    <time dateTime={log.request_time}>
                      {new Date(log.request_time).toLocaleString()}
                    </time>
                  </td>
                  <td className="px-4 py-4">
                    <div className="font-mono break-all">
                      {log.operation_name}
                    </div>
                    <div className="mt-1 text-muted-foreground">
                      {log.enrichments.source === "tool"
                        ? "Agent tool"
                        : "HTTP API"}
                    </div>
                  </td>
                  <td className="max-w-48 px-4 py-4 break-all">{actor(log)}</td>
                  <td className="px-4 py-4">
                    <Outcome value={log.operation_succeeded} />
                  </td>
                  <td className="px-4 py-4">
                    <Button
                      variant="ghost"
                      size="sm"
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
                    className="px-4 py-12 text-center text-muted-foreground"
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
              variant="outline"
              disabled={logs.isFetching}
              onClick={() => void logs.fetchNextPage()}
            >
              {logs.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}
      </section>
      <p className="text-xs/relaxed text-muted-foreground">
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
        <DialogPopup className="max-w-2xl">
          {selected && <AuditDetails log={selected} />}
        </DialogPopup>
      </Dialog>
    </div>
  )
}
