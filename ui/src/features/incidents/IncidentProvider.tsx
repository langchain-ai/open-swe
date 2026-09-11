import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  ErrorState,
  ExternalLink,
  formatTime,
  humanize,
  isReadUnavailable,
} from "./shared"
import { workspaceApi } from "./workspace-api"
import type { ProviderSnapshot } from "./workspace-api"

function ProviderDetails({ snapshot }: { snapshot: ProviderSnapshot }) {
  return (
    <div className="space-y-3">
      <dl className="flex flex-wrap gap-x-8 gap-y-3 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">Incident status</dt>
          <dd className="mt-1">{snapshot.status?.name || "Unknown"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Severity</dt>
          <dd className="mt-1">{snapshot.severity?.name || "Unknown"}</dd>
        </div>
      </dl>
      <ExternalLink href={snapshot.url} className="text-xs">
        Open provider incident
      </ExternalLink>
      {snapshot.postmortem && (
        <details>
          <summary className="cursor-pointer text-xs">
            Provider postmortem
          </summary>
          <p className="mt-3 text-sm leading-7 whitespace-pre-wrap">
            {snapshot.postmortem}
          </p>
        </details>
      )}
    </div>
  )
}

export function IncidentProvider({
  incidentId,
  allowedActions,
}: {
  incidentId: string
  allowedActions: string[]
}) {
  const client = useQueryClient()
  const [connection, setConnection] = useState("")
  const [externalId, setExternalId] = useState("")
  const [status, setStatus] = useState("")
  const [searchText, setSearchText] = useState("")
  const identity = useRef<{ key: string; id: string } | null>(null)
  const provider = useQuery({
    queryKey: ["incidents", "provider", incidentId, "state"],
    queryFn: () => workspaceApi.provider(incidentId),
    refetchInterval: 5000,
    retry: false,
  })
  const connections = useQuery({
    queryKey: ["incidents", "provider", incidentId, "connections"],
    queryFn: () => workspaceApi.connections(incidentId),
    retry: false,
  })
  const defaultConnection = connections.data?.items.find(
    (item) => item.name === provider.data?.default_connection_name
  )?.name
  const selectedConnection =
    connection ||
    provider.data?.binding?.connection_name ||
    defaultConnection ||
    connections.data?.items[0]?.name ||
    ""
  const command = useMutation({
    mutationFn: ({
      action,
      body,
    }: {
      action: "attach" | "refresh" | "status"
      body: Record<string, string>
    }) => {
      const key = JSON.stringify({ action, body })
      if (identity.current?.key !== key)
        identity.current = { key, id: crypto.randomUUID() }
      return workspaceApi.providerCommand(incidentId, action, {
        ...body,
        request_id: identity.current.id,
      })
    },
    onSuccess: () => {
      identity.current = null
    },
  })
  const operation = useQuery({
    queryKey: [
      "incidents",
      "provider",
      incidentId,
      "operation",
      command.data?.command_id,
    ],
    queryFn: () =>
      workspaceApi.providerOperation(incidentId, command.data!.command_id),
    enabled: Boolean(command.data),
    refetchInterval: (query) =>
      !query.state.data ||
      ["accepted", "sending", "unknown"].includes(query.state.data.status)
        ? 2000
        : false,
    retry: false,
  })
  useEffect(() => {
    if (operation.data?.status === "succeeded")
      void client.invalidateQueries({
        queryKey: ["incidents", "provider", incidentId, "state"],
      })
  }, [operation.data?.id, operation.data?.status, client, incidentId])
  const search = useMutation({
    mutationFn: (searchConnection: string) =>
      workspaceApi.searchProvider(
        incidentId,
        searchConnection,
        searchText.trim()
      ),
    onMutate: () => external.reset(),
  })
  const external = useMutation({
    mutationFn: ({
      id,
      connectionName,
    }: {
      id: string
      connectionName: string
    }) => workspaceApi.externalIncident(incidentId, connectionName, id),
  })
  const pending =
    command.isPending ||
    Boolean(
      command.data &&
      (!operation.data ||
        ["accepted", "sending", "unknown"].includes(operation.data.status))
    )
  if (provider.isPending)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading incident provider…
      </p>
    )
  if (provider.error && (!provider.data || !isReadUnavailable(provider.error)))
    return (
      <ErrorState
        error={provider.error}
        retry={() => void provider.refetch()}
      />
    )
  const { binding, snapshot, error, error_kind, last_synced_at, capabilities } =
    provider.data!
  return (
    <section className="space-y-5 rounded-xl border border-border bg-card p-5">
      <div className="flex flex-wrap justify-between gap-3">
        <h2 className="text-sm font-medium">Incident provider</h2>
        {binding && (
          <span className="text-xs text-muted-foreground">
            {binding.connection_name} · {binding.external_id}
          </span>
        )}
      </div>
      {snapshot ? (
        <ProviderDetails snapshot={snapshot} />
      ) : (
        <p className="text-sm text-muted-foreground">
          {error_kind || error
            ? "Provider incident unavailable."
            : "No provider incident attached."}
        </p>
      )}
      {(binding || error_kind || error) && (
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          {(binding || last_synced_at) && (
            <span>
              Last synced:{" "}
              {last_synced_at ? formatTime(last_synced_at) : "Never"}
            </span>
          )}
          {allowedActions.includes("update_provider") && (
            <Button
              variant="outline"
              size="sm"
              disabled={pending && operation.data?.status !== "unknown"}
              onClick={() => command.mutate({ action: "refresh", body: {} })}
            >
              Refresh provider
            </Button>
          )}
        </div>
      )}
      {provider.error && (
        <p role="alert" className="text-sm text-warning-foreground">
          {provider.error.message}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-warning-foreground">
          {humanize(error_kind || "unavailable")}: {error}
        </p>
      )}
      {allowedActions.includes("attach_provider") && (
        <details open={!binding}>
          <summary className="cursor-pointer text-xs">
            {binding
              ? "Change provider attachment"
              : "Attach provider incident"}
          </summary>
          <form
            className="mt-4 space-y-3"
            onSubmit={(event) => {
              event.preventDefault()
              if (selectedConnection && externalId.trim())
                command.mutate({
                  action: "attach",
                  body: {
                    connection_name: selectedConnection,
                    external_id: externalId.trim(),
                  },
                })
            }}
          >
            <label className="block space-y-2 text-xs">
              <span>Workspace connection</span>
              <select
                aria-label="Workspace connection"
                value={selectedConnection}
                onChange={(event) => setConnection(event.target.value)}
                className="block h-9 w-full rounded-md border border-border bg-background px-3"
                disabled={pending || connections.isPending}
              >
                <option value="">Select a connection</option>
                {connections.data?.items.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-2 text-xs">
              <span>Provider incident ID</span>
              <Input
                required
                aria-label="Provider incident ID"
                value={externalId}
                onChange={(event) => setExternalId(event.target.value)}
                disabled={pending}
              />
            </label>
            <Button
              type="submit"
              size="sm"
              disabled={pending || !selectedConnection || !externalId.trim()}
            >
              Attach provider incident
            </Button>
          </form>
        </details>
      )}
      {connections.error && (
        <p role="alert" className="text-xs text-destructive-foreground">
          {connections.error.message}
        </p>
      )}
      {binding &&
        allowedActions.includes("update_provider") &&
        (capabilities.update_status === "supported" ? (
          <form
            className="space-y-3 border-t border-border pt-4"
            onSubmit={(event) => {
              event.preventDefault()
              if (
                status &&
                provider.data?.status_options?.some(
                  (option) => option.id === status
                )
              )
                command.mutate({
                  action: "status",
                  body: { status_id: status.trim() },
                })
            }}
          >
            <label className="block space-y-2 text-xs">
              <span>Provider status</span>
              <select
                aria-label="Provider status"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
                disabled={pending || !provider.data?.status_options?.length}
                className="block h-9 w-full rounded-md border border-border bg-background px-3"
              >
                <option value="">Select a status</option>
                {provider.data?.status_options?.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.name}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-xs text-muted-foreground">
              The displayed status changes after the provider confirms the
              update.
            </p>
            <Button
              type="submit"
              size="sm"
              disabled={
                pending ||
                !status ||
                !provider.data?.status_options?.some(
                  (option) => option.id === status
                )
              }
            >
              Update incident status
            </Button>
          </form>
        ) : (
          <p className="text-xs text-muted-foreground">
            Status updates:{" "}
            {humanize(
              capabilities.update_status || "unsupported"
            ).toLowerCase()}
            .
          </p>
        ))}
      {provider.data?.configuration_error && (
        <p role="alert" className="text-xs text-warning-foreground">
          {provider.data.configuration_error}
        </p>
      )}
      {(command.error || operation.error) && (
        <p role="alert" className="text-sm text-destructive-foreground">
          {(command.error ?? operation.error)?.message}
        </p>
      )}
      {command.data && (
        <p
          role={operation.data?.status === "failed" ? "alert" : "status"}
          className="text-sm"
        >
          {operation.data?.status === "succeeded"
            ? "Provider update confirmed."
            : operation.data?.status === "failed"
              ? operation.data.error || "Provider update failed."
              : operation.data?.status === "unknown"
                ? "Provider outcome is uncertain. Refresh the provider to check whether it was applied."
                : "Provider update pending. Waiting for confirmation."}
        </p>
      )}
      <details className="border-t border-border pt-4" open>
        <summary className="cursor-pointer text-sm font-medium">
          Related provider history
        </summary>
        <p className="mt-3 text-xs text-muted-foreground">
          Browse older incidents for context. Opening a result does not start
          analysis.
        </p>
        {(connections.data?.items.length ?? 0) > 1 && (
          <label className="mt-3 block space-y-2 text-xs">
            <span>History connection</span>
            <select
              aria-label="History connection"
              value={selectedConnection}
              onChange={(event) => {
                setConnection(event.target.value)
                search.reset()
                external.reset()
              }}
              className="block h-9 w-full rounded-md border border-border bg-background px-3"
              disabled={search.isPending || external.isPending}
            >
              {connections.data?.items.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <form
          className="mt-3 flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (selectedConnection) search.mutate(selectedConnection)
          }}
        >
          <Input
            type="search"
            aria-label="Search provider history"
            placeholder="Search previous incidents…"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            className="min-w-40 flex-1"
          />
          <Button
            type="submit"
            size="sm"
            variant="outline"
            disabled={
              !selectedConnection || search.isPending || connections.isPending
            }
          >
            Search provider
          </Button>
        </form>
        {!selectedConnection && !connections.isPending && (
          <p className="mt-3 text-xs text-muted-foreground">
            Connect incident.io in workspace settings to search provider
            history.
          </p>
        )}
        {search.error && (
          <p role="alert" className="mt-3 text-sm text-destructive-foreground">
            {search.error.message}
          </p>
        )}
        {search.data && (
          <div className="mt-3 space-y-3">
            {search.data.items.length ? (
              search.data.items.map((item) => (
                <div
                  key={item.external_id}
                  className="rounded-lg border border-border p-3"
                >
                  <button
                    className="text-left text-sm hover:underline"
                    disabled={external.isPending}
                    onClick={() =>
                      external.mutate({
                        id: item.external_id,
                        connectionName: search.variables || selectedConnection,
                      })
                    }
                  >
                    {item.title || item.external_id}
                  </button>
                  <p className="mt-1 text-xs text-muted-foreground">
                    External incident · {item.status?.name || "Unknown status"}{" "}
                    · {item.severity?.name || "Unknown severity"}
                  </p>
                </div>
              ))
            ) : (
              <p className="text-xs text-muted-foreground">
                No matching provider incidents.
              </p>
            )}
          </div>
        )}
        {search.data?.gaps?.map((gap) => (
          <p key={gap} className="mt-3 text-xs text-warning-foreground">
            {gap}
          </p>
        ))}
        {external.isPending && (
          <p role="status" className="mt-3 text-xs">
            Loading provider incident…
          </p>
        )}
        {external.error && (
          <p role="alert" className="mt-3 text-sm text-destructive-foreground">
            {external.error.message}
          </p>
        )}
        {external.data && (
          <article className="mt-4 space-y-3 rounded-lg bg-muted p-4">
            <h3 className="text-sm font-medium">
              {external.data.incident.title}
            </h3>
            <ProviderDetails snapshot={external.data.incident} />
          </article>
        )}
      </details>
    </section>
  )
}
