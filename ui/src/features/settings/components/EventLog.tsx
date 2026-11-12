import { useState } from "react"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { Text } from "@langchain/macaw-components/Text"
import { Typeahead } from "@langchain/macaw-components/Typeahead"
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"

import {
  api,
  type EventLogFilters,
  type EventLogUser,
  type LoggedEvent,
} from "@/lib/api"

const HOUR = 60 * 60 * 1000
const ANY_SOURCE = "any"
const SOURCE_OPTIONS = [
  { value: ANY_SOURCE, label: "Any source" },
  ...[
    "github",
    "slack",
    "linear",
    "deployment",
    "workspace",
    "human_review",
    "reviewer",
  ].map((source) => ({ value: source, label: source })),
]
const MAX_RANGE = 15 * 24 * HOUR

type FilterKey =
  | "source"
  | "event_type"
  | "event_type_prefix"
  | "delivery_id"
  | "user_id"
  | "workspace_id"
  | "repository_id"
  | "pull_request_id"
type Filters = Partial<Record<FilterKey, { value: string; label: string }>>

const FILTER_NAMES: Record<FilterKey, string> = {
  source: "Source",
  event_type: "Event type",
  event_type_prefix: "Type prefix",
  delivery_id: "Delivery",
  user_id: "User",
  workspace_id: "Environment",
  repository_id: "Repository",
  pull_request_id: "Pull request",
}

class TimeRange {
  constructor(
    readonly start_time: string,
    readonly end_time: string
  ) {}

  static lastHour() {
    const end = new Date()
    return new TimeRange(
      TimeRange.local(new Date(end.getTime() - HOUR)),
      TimeRange.local(end)
    )
  }

  static local(date: Date) {
    return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
      .toISOString()
      .slice(0, 19)
  }

  with(key: "start_time" | "end_time", value: string) {
    return key === "start_time"
      ? new TimeRange(value, this.end_time)
      : new TimeRange(this.start_time, value)
  }

  query(filters: Filters): EventLogFilters {
    return {
      ...Object.fromEntries(
        Object.entries(filters).map(([key, filter]) => [key, filter.value])
      ),
      start_time: new Date(this.start_time).toISOString(),
      end_time: new Date(this.end_time).toISOString(),
    }
  }
}

function UserFilter({ onSelect }: { onSelect: (user: EventLogUser) => void }) {
  const [query, setQuery] = useState("")
  const users = useQuery({
    queryKey: ["eventLogUsers", query],
    queryFn: () => api.searchEventLogUsers(query),
    enabled: query.trim().length > 0,
    staleTime: 60_000,
  })
  const byId = new Map((users.data ?? []).map((user) => [user.id, user]))
  return (
    <Typeahead<string>
      aria-label="Filter by user"
      size="md"
      placeholder="Filter by user name, login or email"
      options={[...byId.keys()]}
      value={null}
      inputValue={query}
      onInputChange={setQuery}
      filterOptions={(ids) => ids}
      emptyText={query ? "No matching people" : "Type to search"}
      getOptionLabel={(id) => byId.get(id)?.display_name || id}
      renderOption={(id) => {
        const user = byId.get(id)
        return (
          <span className="flex min-w-0 flex-col">
            <span className="truncate">{user?.display_name || id}</span>
            {user?.logins && (
              <span className="truncate text-xs text-secondary">
                {user.logins}
              </span>
            )}
          </span>
        )
      }}
      onChange={(id) => {
        const user = id ? byId.get(id) : undefined
        if (user) onSelect(user)
        setQuery("")
      }}
    />
  )
}

function FilterCell({
  value,
  label,
  title,
  onFilter,
}: {
  value: string | null
  label: string | null
  title?: string | null
  onFilter: () => void
}) {
  if (!value) return <span className="text-tertiary">—</span>
  return (
    <button
      type="button"
      title={title ?? `Filter by ${label ?? value}`}
      className="block max-w-56 cursor-pointer truncate text-left hover:underline"
      onClick={onFilter}
    >
      {label ?? value}
    </button>
  )
}

function EventTypeCell({
  eventType,
  onPrefix,
  onExact,
}: {
  eventType: string
  onPrefix: (prefix: string) => void
  onExact: () => void
}) {
  if (!eventType) return <span className="text-tertiary">—</span>
  const dot = eventType.indexOf(".")
  const base = dot < 0 ? eventType : eventType.slice(0, dot)
  const action = dot < 0 ? "" : eventType.slice(dot + 1)
  return (
    <span className="flex max-w-64 min-w-0">
      <button
        type="button"
        title={`Filter by ${base}.*`}
        className="cursor-pointer truncate hover:underline"
        onClick={() => onPrefix(base)}
      >
        {base}
      </button>
      {action && (
        <button
          type="button"
          title={`Filter by ${eventType}`}
          className="cursor-pointer truncate text-secondary hover:underline"
          onClick={onExact}
        >
          .{action}
        </button>
      )}
    </span>
  )
}

function EventDetails({
  event,
  onFilter,
}: {
  event: LoggedEvent
  onFilter: (key: FilterKey, value: string, label: string) => void
}) {
  const fields: [string, string | null, FilterKey | null][] = [
    ["Received (UTC)", event.received_at, null],
    ["Source", event.source, null],
    ["Event type", event.event_type || null, null],
    ["Delivery ID", event.delivery_id || null, "delivery_id"],
    ["User ID", event.user_id, null],
    ["Environment ID", event.workspace_id, null],
    ["Repository ID", event.repository_id, null],
    ["Pull request ID", event.pull_request_id, null],
    ["Pull request title", event.pull_request_title, null],
    ["Visibility", event.visibility, null],
    ["Visible to user ID", event.visible_to_user_id, null],
  ]
  return (
    <div className="space-y-space-5">
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-space-5 gap-y-space-3 text-xs">
        {fields
          .filter(([, value]) => value != null)
          .map(([label, value, key]) => (
            <div key={label} className="contents">
              <dt className="text-secondary">{label}</dt>
              <dd className="font-mono break-all text-primary">
                {key && value ? (
                  <FilterCell
                    value={value}
                    label={value}
                    onFilter={() => onFilter(key, value, value)}
                  />
                ) : (
                  value
                )}
              </dd>
            </div>
          ))}
      </dl>
      <pre className="max-h-[50vh] overflow-auto rounded-md border border-default bg-surface-level-2 p-space-3 text-xs text-primary">
        {JSON.stringify(event.payload, null, 2)}
      </pre>
    </div>
  )
}

export function EventLog() {
  const [draft, setDraft] = useState(TimeRange.lastHour)
  const [range, setRange] = useState(draft)
  const [filters, setFilters] = useState<Filters>({})
  const [validation, setValidation] = useState("")
  const [selected, setSelected] = useState<LoggedEvent | null>(null)
  const [prefixDraft, setPrefixDraft] = useState("")
  const query = range.query(filters)
  const events = useInfiniteQuery({
    queryKey: ["eventLog", query],
    queryFn: ({ pageParam }) => api.searchEventLog(query, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.cursor ?? undefined,
    refetchOnWindowFocus: false,
    retry: false,
  })
  const items = events.data?.pages.flatMap((page) => page.items) ?? []
  const addFilter = (key: FilterKey, value: string, label: string) => {
    const next = { ...filters, [key]: { value, label } }
    if ((key === "event_type" || key === "delivery_id") && !next.source) {
      return
    }
    setFilters(next)
    setSelected(null)
  }
  const removeFilter = (key: FilterKey) => {
    const next = { ...filters }
    delete next[key]
    if (key === "source") {
      delete next.event_type
      delete next.delivery_id
    }
    setFilters(next)
  }
  const filterEntries = Object.entries(filters) as [
    FilterKey,
    { value: string; label: string },
  ][]

  return (
    <div className="space-y-space-5">
      <form
        className="space-y-space-4 rounded-lg border border-default bg-surface-level-1 p-space-4"
        onSubmit={(event) => {
          event.preventDefault()
          const start = new Date(draft.start_time).getTime()
          const end = new Date(draft.end_time).getTime()
          if (
            !Number.isFinite(start) ||
            !Number.isFinite(end) ||
            start > end ||
            end - start > MAX_RANGE
          ) {
            setValidation("Choose an ordered time range of at most 15 days.")
            return
          }
          setValidation("")
          setRange(draft)
        }}
      >
        <div className="flex flex-wrap items-end gap-space-3">
          {(["start_time", "end_time"] as const).map((key) => (
            <label
              key={key}
              className="min-w-0 basis-full space-y-space-2 text-xs font-medium text-primary sm:flex-1 sm:basis-auto"
            >
              <span>{key === "start_time" ? "From" : "To"} (local time)</span>
              <Input
                type="datetime-local"
                size="md"
                step="1"
                required
                value={draft[key]}
                onChange={(value) => setDraft(draft.with(key, value))}
              />
            </label>
          ))}
          <Button
            type="button"
            size="md"
            color="secondary"
            variant="outlined"
            onClick={() => setDraft(TimeRange.lastHour())}
          >
            Last hour
          </Button>
          <Button type="submit" size="md" color="primary">
            Apply
          </Button>
        </div>
        <div className="grid gap-space-3 sm:grid-cols-[12rem_minmax(0,1fr)_minmax(0,1fr)]">
          <Select
            aria-label="Filter by source"
            options={SOURCE_OPTIONS}
            value={filters.source?.value ?? ANY_SOURCE}
            onChange={(value) =>
              !value || value === ANY_SOURCE
                ? removeFilter("source")
                : addFilter("source", value, value)
            }
          />
          <Input
            aria-label="Filter by event type prefix"
            size="md"
            placeholder="Event type prefix, e.g. pull_request"
            maxLength={256}
            value={prefixDraft}
            onChange={setPrefixDraft}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return
              event.preventDefault()
              const prefix = prefixDraft.trim()
              if (prefix) addFilter("event_type_prefix", prefix, `${prefix}.*`)
              else removeFilter("event_type_prefix")
              setPrefixDraft("")
            }}
          />
          <UserFilter
            onSelect={(user) =>
              addFilter("user_id", user.id, user.display_name || user.id)
            }
          />
        </div>
        {validation && (
          <p role="alert" className="text-xs text-error-secondary">
            {validation}
          </p>
        )}
      </form>

      <section
        className="space-y-space-3"
        aria-label="Events"
        aria-busy={events.isFetching}
      >
        <div className="flex items-center justify-between gap-space-3">
          <div className="space-y-space-1">
            <Text as="h2" variant="h5" weight="medium" color="primary">
              Events
            </Text>
            <p className="text-xs text-secondary">
              {items.length} loaded · Newest first · Click a value to filter by
              it
            </p>
          </div>
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            leftDecorator={ArrowClockwiseIcon}
            disabled={events.isFetching}
            onClick={() => void events.refetch()}
          >
            Reload
          </Button>
        </div>
        {filterEntries.length > 0 && (
          <div className="flex flex-wrap items-center gap-space-2">
            {filterEntries.map(([key, filter]) => (
              <Button
                key={key}
                size="xs"
                color="secondary"
                variant="outlined"
                rightDecorator={XIcon}
                aria-label={`Remove ${FILTER_NAMES[key]} filter`}
                onClick={() => removeFilter(key)}
              >
                {FILTER_NAMES[key]}: {filter.label}
              </Button>
            ))}
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              onClick={() => setFilters({})}
            >
              Clear all
            </Button>
          </div>
        )}
        {events.isError && (
          <div role="alert">
            <Banner intent="error" title="Could not load the event log.">
              {events.error.message}
            </Banner>
          </div>
        )}
        <div className="overflow-x-auto rounded-lg border border-default bg-surface-level-1">
          <table className="w-full min-w-[960px] text-left text-xs">
            <thead className="border-b border-default bg-surface-level-2 text-secondary">
              <tr>
                {[
                  "Time",
                  "Source",
                  "Event type",
                  "User",
                  "Environment",
                  "Repository",
                  "Pull request",
                  "",
                ].map((heading) => (
                  <th
                    key={heading}
                    scope="col"
                    className="px-space-3 py-space-3 font-medium"
                  >
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-subtle text-primary">
              {items.map((event) => (
                <tr
                  key={`${event.received_at}:${event.source}:${event.delivery_id}`}
                  className="hover:bg-surface-level-1-hover"
                >
                  <td className="px-space-3 py-space-3 whitespace-nowrap">
                    <time dateTime={event.received_at}>
                      {new Date(event.received_at).toLocaleString()}
                    </time>
                  </td>
                  <td className="px-space-3 py-space-3">
                    <FilterCell
                      value={event.source}
                      label={event.source}
                      onFilter={() =>
                        addFilter("source", event.source, event.source)
                      }
                    />
                  </td>
                  <td className="px-space-3 py-space-3 font-mono">
                    <EventTypeCell
                      eventType={event.event_type}
                      onPrefix={(prefix) =>
                        addFilter("event_type_prefix", prefix, `${prefix}.*`)
                      }
                      onExact={() =>
                        setFilters({
                          ...filters,
                          source: { value: event.source, label: event.source },
                          event_type: {
                            value: event.event_type,
                            label: event.event_type,
                          },
                        })
                      }
                    />
                  </td>
                  <td className="px-space-3 py-space-3">
                    <FilterCell
                      value={event.user_id}
                      label={event.user_name || event.user_id}
                      onFilter={() =>
                        event.user_id &&
                        addFilter(
                          "user_id",
                          event.user_id,
                          event.user_name || event.user_id
                        )
                      }
                    />
                  </td>
                  <td className="px-space-3 py-space-3">
                    <FilterCell
                      value={event.workspace_id}
                      label={event.workspace_name || event.workspace_id}
                      onFilter={() =>
                        event.workspace_id &&
                        addFilter(
                          "workspace_id",
                          event.workspace_id,
                          event.workspace_name || event.workspace_id
                        )
                      }
                    />
                  </td>
                  <td className="px-space-3 py-space-3">
                    <FilterCell
                      value={event.repository_id}
                      label={event.repository_name || event.repository_id}
                      onFilter={() =>
                        event.repository_id &&
                        addFilter(
                          "repository_id",
                          event.repository_id,
                          event.repository_name || event.repository_id
                        )
                      }
                    />
                  </td>
                  <td className="px-space-3 py-space-3">
                    <FilterCell
                      value={event.pull_request_id}
                      label={event.pull_request_name || event.pull_request_id}
                      title={event.pull_request_title}
                      onFilter={() =>
                        event.pull_request_id &&
                        addFilter(
                          "pull_request_id",
                          event.pull_request_id,
                          event.pull_request_name || event.pull_request_id
                        )
                      }
                    />
                  </td>
                  <td className="px-space-3 py-space-3 text-right">
                    {event.visibility !== "public" && (
                      <Badge size="xs" color="secondary">
                        {event.visibility}
                      </Badge>
                    )}{" "}
                    <Button
                      size="xs"
                      color="secondary"
                      variant="plain"
                      aria-label={`View ${event.source} ${event.event_type} event`}
                      onClick={() => setSelected(event)}
                    >
                      View
                    </Button>
                  </td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr>
                  <td
                    colSpan={8}
                    className="px-space-4 py-space-8 text-center text-secondary"
                  >
                    {events.isPending
                      ? "Loading events…"
                      : events.isError
                        ? "Event log unavailable."
                        : "No events match this time range and filters."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {events.hasNextPage && (
          <div className="flex justify-center">
            <Button
              size="md"
              color="secondary"
              variant="outlined"
              disabled={events.isFetching}
              onClick={() => void events.fetchNextPage()}
            >
              {events.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}
      </section>
      <Dialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
      >
        <DialogContent
          title={
            <span className="break-all">
              {selected?.source} {selected?.event_type}
            </span>
          }
          description="Logged delivery and payload"
          className="w-[48rem] max-w-[calc(100vw-2rem)]"
        >
          {selected && (
            <EventDetails
              event={selected}
              onFilter={(key, value, label) => {
                setFilters({
                  ...filters,
                  source: { value: selected.source, label: selected.source },
                  [key]: { value, label },
                })
                setSelected(null)
              }}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
