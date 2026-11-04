import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"

import { useScopedSettings } from "@/features/settings/lib/settingsScope"
import { optimisticUpdate } from "@/lib/optimistic"
import {
  api,
  type BotServer,
  type NewBotServer,
  type WorkspaceBotTools,
} from "@/lib/api"
import { managedToolsQuery, useConnectPopup } from "./ManagedToolsSection"

const botToolsKey = (slug: string) => ["workspaceBotTools", slug]
const KITS_KEY = ["managedToolsKits"]
const NEW_SERVER = "new-server"
const AUTH_KINDS = [
  { value: "oauth", label: "OAuth sign-in" },
  { value: "secret", label: "API key header" },
  { value: "none", label: "None (public)" },
] as const
const EMPTY: NewBotServer = { name: "", url: "", kind: "oauth", header: "" }

/**
 * The workspace bot's MCP servers, shown first in a workspace's MCP Servers: the
 * servers in its LangSmith Managed Tools kit, which every run in the workspace
 * loads, connected as the LMT profile Open SWE keeps for the workspace.
 */
export function WorkspaceBotServers({ slug }: { slug: string }) {
  const qc = useQueryClient()
  const key = botToolsKey(slug)
  const managed = useQuery(managedToolsQuery)
  const bot = useQuery({
    queryKey: key,
    queryFn: () => api.getWorkspaceBotTools(slug),
  })
  const settings = useScopedSettings({ kind: "workspace", slug }, () => {
    void qc.invalidateQueries({ queryKey: key })
  })
  const kits = useQuery({
    queryKey: KITS_KEY,
    queryFn: api.listManagedToolsKits,
    retry: false,
  })
  const connect = useConnectPopup(key, (serverId) =>
    api.connectWorkspaceBotTool(slug, serverId)
  )
  const [draft, setDraft] = useState<NewBotServer | null>(null)
  const [picked, setPicked] = useState<string | undefined>()
  const [editing, setEditing] = useState<string | null>(null)
  const add = useConnectPopup(key, async () => {
    if (!draft) return { connected: false }
    const added = await api.addWorkspaceBotServer(slug, {
      ...draft,
      header: draft.kind === "secret" ? draft.header : null,
    })
    setDraft(null)
    return { connected: !added.url, url: added.url }
  })
  const remove = useMutation({
    meta: { errorTitle: "Couldn't remove the server" },
    mutationFn: (serverId: string) =>
      api.removeWorkspaceBotTool(slug, serverId),
    onMutate: async (serverId) => ({
      undo: await optimisticUpdate<WorkspaceBotTools>(qc, key, (current) => ({
        ...current,
        status: current.status && {
          ...current.status,
          servers: current.status.servers.map((server) =>
            server.id === serverId
              ? { ...server, tools: [], connected: server.kind === "none" }
              : server
          ),
        },
      })),
    }),
    onError: (_e, _v, ctx) => ctx?.undo(),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: key })
    },
  })

  if (managed.data && !managed.data.configured) return null
  if (bot.isLoading) {
    return <p className="text-sm text-secondary">Loading servers…</p>
  }
  if (bot.isError || bot.data?.error) {
    return (
      <p role="alert" className="text-sm text-error-secondary">
        {`Managed tools servers could not be read: ${bot.data?.error ?? bot.error?.message}`}
      </p>
    )
  }
  const status = bot.data?.status
  if (!status) {
    return (
      <p className="text-sm text-secondary">
        Open SWE has no LangSmith API key, so this workspace can&apos;t use
        LangSmith Managed Tools servers.
      </p>
    )
  }

  const kitId = settings.data?.managed_tools_gateway_id ?? null
  const mine = status.servers.filter(
    (server) => server.tools.length > 0 || server.connected
  )
  const others = status.servers.filter(
    (server) => server.enabled && server.tools.length === 0 && !server.connected
  )
  const chosen = others.find((server) => server.id === picked)
  const addable =
    !!draft &&
    draft.name.trim() !== "" &&
    draft.url.startsWith("https://") &&
    (draft.kind !== "secret" || !!draft.header?.trim())

  return (
    <div className="space-y-space-4">
      <div className="flex flex-wrap items-center justify-between gap-space-3">
        <p className="text-xs text-secondary">
          Servers in this workspace&apos;s LangSmith tool kit load in every run
          here, signed in as the workspace bot. Someone&apos;s own connection to
          a server replaces the bot&apos;s in their private threads.
        </p>
        {kits.data && kits.data.length > 0 ? (
          <Select
            aria-label="Tool kit"
            size="sm"
            options={[
              ...kits.data.map((kit) => ({ value: kit.id, label: kit.name })),
              ...(kitId && !kits.data.some((kit) => kit.id === kitId)
                ? [{ value: kitId, label: "Configured tool kit" }]
                : []),
            ]}
            value={kitId ?? undefined}
            placeholder="New tool kit"
            onChange={(next) =>
              next && settings.save({ managed_tools_gateway_id: next })
            }
            disabled={!settings.data}
            triggerClassName="w-56"
          />
        ) : null}
      </div>
      {mine.map((server) => (
        <BotServerCard
          key={server.id}
          slug={slug}
          server={server}
          editing={editing === server.id}
          onEdit={() => setEditing(editing === server.id ? null : server.id)}
          connecting={connect.connecting === server.id}
          onConnect={() => connect.start(server.id)}
          onRemove={() => remove.mutate(server.id)}
        />
      ))}
      {draft ? (
        <div className="space-y-space-3 rounded-md border border-default p-space-3">
          <Input
            label="Name"
            aria-label="Server name"
            size="md"
            required
            maxLength={100}
            value={draft.name}
            onChange={(name) => setDraft({ ...draft, name })}
          />
          <Input
            label="Server URL"
            aria-label="Server URL"
            size="md"
            required
            type="url"
            placeholder="https://example.com/mcp"
            value={draft.url}
            onChange={(url) => setDraft({ ...draft, url })}
          />
          <Select
            aria-label="Authentication"
            size="md"
            options={[...AUTH_KINDS]}
            value={draft.kind}
            onChange={(kind) => kind && setDraft({ ...draft, kind })}
          />
          {draft.kind === "secret" ? (
            <Input
              label="Header"
              aria-label="API key header"
              size="md"
              required
              placeholder="X-API-Key"
              value={draft.header ?? ""}
              onChange={(header) => setDraft({ ...draft, header })}
            />
          ) : null}
          <p className="text-xs text-secondary">
            Adds the server to your LangSmith workspace, then opens LangSmith to
            sign it in for this workspace&apos;s bot; you enter any API key
            there. Its tools join the bot once it&apos;s connected.
          </p>
          <div className="flex flex-wrap gap-space-2">
            <Button
              size="xs"
              color="primary"
              disabled={!addable || add.connecting === NEW_SERVER}
              onClick={() => add.start(NEW_SERVER)}
            >
              {add.connecting === NEW_SERVER ? "Adding…" : "Add server"}
            </Button>
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              onClick={() => setDraft(null)}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-space-2">
          <Button size="xs" color="primary" onClick={() => setDraft(EMPTY)}>
            Add MCP server
          </Button>
          {others.length > 0 ? (
            <>
              <Select
                aria-label="Server from LangSmith"
                size="sm"
                options={others.map((server) => ({
                  value: server.id,
                  label: server.name,
                }))}
                value={chosen?.id}
                onChange={(next) => setPicked(next ?? undefined)}
                placeholder="Add from LangSmith"
                triggerClassName="w-56"
              />
              {chosen ? (
                <Button
                  size="xs"
                  color="secondary"
                  variant="outlined"
                  disabled={connect.connecting === chosen.id}
                  onClick={() => {
                    setPicked(undefined)
                    connect.start(chosen.id)
                  }}
                >
                  {connect.connecting === chosen.id
                    ? "Waiting…"
                    : chosen.kind === "none"
                      ? "Add"
                      : "Add and connect"}
                </Button>
              ) : null}
            </>
          ) : null}
        </div>
      )}
    </div>
  )
}

function BotServerCard({
  slug,
  server,
  editing,
  onEdit,
  connecting,
  onConnect,
  onRemove,
}: {
  slug: string
  server: BotServer
  editing: boolean
  onEdit: () => void
  connecting: boolean
  onConnect: () => void
  onRemove: () => void
}) {
  const state = !server.connected
    ? server.kind === "secret"
      ? "Needs the bot's API key"
      : "Not connected for the bot"
    : server.tools.length === 0
      ? "Connected · no tools yet"
      : `Connected · ${server.tools.length} ${server.tools.length === 1 ? "tool" : "tools"}`
  return (
    <section
      aria-label={`${server.name} MCP server`}
      className="rounded-md border border-default"
    >
      <div className="flex flex-wrap items-center justify-between gap-space-3 p-space-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-primary">
            {server.name} <span className="text-secondary">· {state}</span>
          </p>
          <p className="text-xs text-secondary">LangSmith Managed Tools</p>
        </div>
        <div className="flex gap-space-2">
          {!server.connected ? (
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              disabled={connecting}
              onClick={onConnect}
              aria-label={`Connect ${server.name} for the bot`}
            >
              {connecting ? "Waiting…" : "Connect"}
            </Button>
          ) : (
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              onClick={onEdit}
              aria-expanded={editing}
              aria-label={`${editing ? "Close" : "Edit tools for"} ${server.name}`}
            >
              {editing
                ? "Close"
                : server.tools.length === 0
                  ? "Add tools"
                  : "Edit tools"}
            </Button>
          )}
          <Button
            size="xs"
            color="error"
            variant="outlined"
            onClick={onRemove}
            aria-label={`Remove ${server.name}`}
          >
            Remove
          </Button>
        </div>
      </div>
      {editing ? (
        <BotServerTools slug={slug} server={server} onDone={onEdit} />
      ) : null}
    </section>
  )
}

function BotServerTools({
  slug,
  server,
  onDone,
}: {
  slug: string
  server: BotServer
  onDone: () => void
}) {
  const qc = useQueryClient()
  const tools = useQuery({
    queryKey: ["workspaceBotServerTools", slug, server.id],
    queryFn: () => api.getWorkspaceBotServerTools(slug, server.id),
  })
  const [selected, setSelected] = useState<ReadonlySet<string> | null>(null)
  const chosen =
    selected ??
    new Set(
      server.tools.length > 0 ? server.tools : (tools.data?.available ?? [])
    )
  const save = useMutation({
    meta: { errorTitle: "Couldn't save the tools" },
    mutationFn: () =>
      api.setWorkspaceBotServerTools(slug, server.id, [...chosen]),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: botToolsKey(slug) })
      onDone()
    },
  })
  const available = tools.data?.available ?? []
  return (
    <div className="space-y-space-3 border-t border-muted p-space-3">
      {tools.isLoading ? (
        <p className="text-xs text-secondary">Listing tools…</p>
      ) : tools.isError ? (
        <p role="alert" className="text-xs text-error-secondary">
          {tools.error.message}
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-space-2">
            <span className="mr-auto text-xs text-secondary">
              {chosen.size} of {available.length} selected
            </span>
            <Button
              size="sm"
              color="secondary"
              variant="outlined"
              disabled={chosen.size === available.length}
              onClick={() => setSelected(new Set(available))}
            >
              Select all
            </Button>
            <Button
              size="sm"
              color="secondary"
              variant="outlined"
              disabled={chosen.size === 0}
              onClick={() => setSelected(new Set())}
            >
              Clear all
            </Button>
          </div>
          <div
            role="region"
            aria-label={`${server.name} tools`}
            tabIndex={0}
            className="max-h-80 space-y-space-2 overflow-y-auto overscroll-contain rounded-md border border-default p-space-3"
          >
            {available.map((name) => (
              <label
                key={name}
                className="flex items-start gap-space-2 text-sm text-primary"
              >
                <Checkbox
                  containerClassName="mt-0.5 shrink-0"
                  aria-label={`Allow ${name}`}
                  checked={chosen.has(name)}
                  onCheckedChange={(checked) => {
                    const next = new Set(chosen)
                    if (checked === true) next.add(name)
                    else next.delete(name)
                    setSelected(next)
                  }}
                />
                <span className="min-w-0 break-words">{name}</span>
              </label>
            ))}
          </div>
          <div className="flex gap-space-2">
            <Button
              size="sm"
              color="primary"
              disabled={save.isPending}
              onClick={() => save.mutate()}
            >
              {chosen.size === 0 ? "Save (removes the server)" : "Save tools"}
            </Button>
            <Button
              size="sm"
              color="secondary"
              variant="plain"
              onClick={onDone}
            >
              Cancel
            </Button>
          </div>
        </>
      )}
    </div>
  )
}
