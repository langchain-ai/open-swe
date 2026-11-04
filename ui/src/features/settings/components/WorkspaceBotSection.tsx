import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { optimisticUpdate } from "@/lib/optimistic"
import {
  api,
  type ManagedServer,
  type NewBotServer,
  type WorkspaceBotTools,
} from "@/lib/api"
import {
  ConnectButton,
  managedToolsQuery,
  useConnectPopup,
} from "./ManagedToolsSection"

const botToolsKey = (slug: string) => ["workspaceBotTools", slug]
const NEW_SERVER = "new-server"
const AUTH_KINDS = [
  { value: "oauth", label: "OAuth sign-in" },
  { value: "secret", label: "API key header" },
] as const
const EMPTY: NewBotServer = { name: "", url: "", kind: "oauth", header: "" }

/**
 * The workspace bot: the LangSmith Managed Tools services every run in the
 * workspace loads, connected as an LMT profile Open SWE makes on the first connect.
 */
export function WorkspaceBotSection({ slug }: { slug: string }) {
  const qc = useQueryClient()
  const key = botToolsKey(slug)
  const managed = useQuery(managedToolsQuery)
  const bot = useQuery({
    queryKey: key,
    queryFn: () => api.getWorkspaceBotTools(slug),
  })
  const popup = useConnectPopup(key, (serverId) =>
    api.connectWorkspaceBotTool(slug, serverId)
  )
  const [draft, setDraft] = useState<NewBotServer | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const add = useConnectPopup(key, async () => {
    if (!draft) return { connected: false }
    const added = await api.addWorkspaceBotServer(slug, {
      ...draft,
      header: draft.kind === "secret" ? draft.header : null,
    })
    setDraft(null)
    return { connected: false, url: added.url }
  })
  const disconnect = useMutation({
    meta: { errorTitle: "Couldn't disconnect the service" },
    mutationFn: (serverId: string) =>
      api.disconnectWorkspaceBotTool(slug, serverId),
    onMutate: async (serverId) => ({
      undo: await optimisticUpdate<WorkspaceBotTools>(qc, key, (current) => ({
        ...current,
        status: current.status && {
          ...current.status,
          servers: current.status.servers.map((server) =>
            server.id === serverId ? { ...server, connected: false } : server
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

  const status = bot.data?.status
  const services = status?.servers ?? []
  const connected = services.filter((server) => server.connected)
  const available = services.filter(
    (server) => server.enabled && !server.connected
  )
  const chosen = available.find((server) => server.id === picked)
  const addable =
    !!draft &&
    draft.name.trim() !== "" &&
    draft.url.startsWith("https://") &&
    (draft.kind === "oauth" || !!draft.header?.trim())

  return (
    <SettingsSection
      title="Workspace bot"
      description="LangSmith Managed Tools services every run in this workspace can use, signed in as the workspace bot rather than as you. In someone's private threads, a service they connected themselves replaces the bot's service of the same name."
    >
      {bot.isLoading ? (
        <p className="p-space-4 text-xs text-secondary">Loading…</p>
      ) : bot.isError || bot.data?.error ? (
        <p role="alert" className="p-space-4 text-xs text-error-secondary">
          {`The bot's services could not be read: ${bot.data?.error ?? bot.error?.message}`}
        </p>
      ) : !status ? (
        <p className="p-space-4 text-xs text-secondary">
          Open SWE has no LangSmith API key, so the workspace bot can&apos;t
          load services.
        </p>
      ) : (
        <>
          {connected.map((server) => (
            <BotServerRow
              key={server.id}
              server={server}
              onDisconnect={(serverId) => disconnect.mutate(serverId)}
            />
          ))}
          <SettingsRow
            label="Connect a service"
            description={
              connected.length === 0
                ? "The bot has no services yet. Each one you connect loads in every run in this workspace."
                : "Signs a service in for the bot, not for you."
            }
            control={
              <div className="flex items-center gap-space-2">
                <Select
                  aria-label="Service to connect"
                  options={available.map((server) => ({
                    value: server.id,
                    label: server.name,
                  }))}
                  value={chosen?.id}
                  onChange={(next) => setPicked(next ?? null)}
                  placeholder="Choose a service"
                  triggerClassName="w-56"
                />
                {chosen ? (
                  <ConnectButton
                    server={chosen}
                    label="Connect for bot"
                    connecting={popup.connecting === chosen.id}
                    onConnect={popup.start}
                  />
                ) : null}
              </div>
            }
          />
        </>
      )}
      {draft ? (
        <div className="flex flex-col gap-space-3 p-space-4">
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
            sign it in for this workspace&apos;s bot. You enter any API key
            there, not here.
          </p>
          <div className="flex items-center gap-space-2">
            <Button
              size="xs"
              color="primary"
              disabled={!addable || add.connecting === NEW_SERVER}
              onClick={() => add.start(NEW_SERVER)}
            >
              {add.connecting === NEW_SERVER ? "Adding…" : "Add and connect"}
            </Button>
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              onClick={() => setDraft(null)}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-between gap-space-2 px-space-4 py-space-3">
          <p className="text-xs text-secondary">
            Services come from your LangSmith workspace&apos;s Tools. Add one
            that isn&apos;t there yet.
          </p>
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            disabled={!status}
            onClick={() => setDraft(EMPTY)}
          >
            Add MCP server
          </Button>
        </div>
      )}
    </SettingsSection>
  )
}

function BotServerRow({
  server,
  onDisconnect,
}: {
  server: ManagedServer
  onDisconnect: (serverId: string) => void
}) {
  return (
    <SettingsRow
      label={server.name}
      description="Loads in every run in this workspace"
      control={
        <div className="flex items-center gap-space-2">
          <Badge color="primary" size="xs">
            Connected
          </Badge>
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            aria-label={`Disconnect ${server.name} for the bot`}
            onClick={() => onDisconnect(server.id)}
          >
            Disconnect
          </Button>
        </div>
      }
    />
  )
}
