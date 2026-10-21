import {
  CONNECT_MESSAGE,
  PROTOCOL_VERSION,
  type HostContext,
  type HostEvent,
  type HostMessage,
} from "@openswe/plugin-sdk"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { useQueries, useQuery } from "@tanstack/react-query"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"
import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import type { InstalledPlugin } from "@/features/plugins/lib/api"
import { pluginsApi } from "@/features/plugins/lib/api"
import { PluginRooms } from "@/features/plugins/lib/rooms"
import type { BridgeHost, ThreadRequest } from "@/features/plugins/lib/bridge"
import {
  BridgeRequestError,
  handleBridgeRequest,
} from "@/features/plugins/lib/bridge"
import {
  PLUGIN_ALLOW,
  PLUGIN_SANDBOX,
  pluginDocument,
} from "@/features/plugins/lib/shell"
import { DashboardRequestError } from "@/lib/dashboard-fetch"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { useSession } from "@/lib/session"
import { useResolvedTheme } from "@/lib/theme"

function errorOf(error: unknown): { message: string; status: number | null } {
  if (error instanceof BridgeRequestError)
    return { message: error.message, status: error.status }
  if (error instanceof DashboardRequestError)
    return { message: error.message, status: error.status }
  return {
    message: error instanceof Error ? error.message : "Request failed",
    status: null,
  }
}

/**
 * One plugin's sandboxed frame and the port it talks to the dashboard over.
 *
 * The frame is connected once. Should it load a second document, it navigated
 * itself away from the bundle, out from under its policy, so it is torn down
 * rather than handed a new port.
 */
export function PluginFrame({
  plugin,
  html,
  path,
  navigate,
  openThread,
  confirmThread,
  confirmLink,
}: {
  plugin: InstalledPlugin
  html: string
  path: string
  navigate: (path: string, replace: boolean) => void
  openThread: (threadId: string) => void
  confirmThread: (request: ThreadRequest) => Promise<boolean>
  confirmLink: (url: string) => Promise<boolean>
}) {
  const session = useSession()
  const theme = useResolvedTheme()
  const frameRef = useRef<HTMLIFrameElement>(null)
  const portRef = useRef<MessagePort | null>(null)
  const loads = useRef(0)
  const sentPath = useRef(path)
  const [generation, setGeneration] = useState(0)
  const [escaped, setEscaped] = useState(false)
  const [watched, setWatched] = useState<ReadonlyArray<string | null>>([])
  const [initialTheme] = useState(theme)
  const rooms = useRef<PluginRooms | null>(null)
  const login = session.data?.login ?? ""
  const me = useQuery({
    queryKey: ["plugin-person", plugin.id, login],
    queryFn: async () =>
      (await pluginsApi.people(plugin.id, [login]))[0] ?? null,
    enabled: Boolean(login),
    staleTime: Infinity,
  })
  const srcDoc = useMemo(
    () => pluginDocument(html, initialTheme),
    [html, initialTheme]
  )

  const post = (message: HostMessage) => portRef.current?.postMessage(message)
  const emit = useEffectEvent((event: HostEvent) =>
    post({ kind: "event", event })
  )

  const context = (): HostContext => ({
    protocol: PROTOCOL_VERSION,
    plugin: {
      id: plugin.id,
      name: plugin.name,
      displayName: plugin.manifest.displayName,
      version: plugin.manifest.version,
      permissions: plugin.manifest.permissions,
      repositories: plugin.repositories,
    },
    user: me.data ?? { login, name: login, avatar: null },
    theme,
    path: sentPath.current,
  })

  const host: BridgeHost = {
    plugin,
    navigate: (next, replace) => {
      sentPath.current = next
      navigate(next, replace)
    },
    openThread,
    toast: ({ title, description, intent }) => {
      const show =
        intent === "success"
          ? toast.success
          : intent === "error"
            ? toast.error
            : toast.info
      show(title, { description })
    },
    confirmThread,
    confirmLink,
    watch: (repository) =>
      setWatched((prev) =>
        prev.includes(repository) ? prev : [...prev, repository]
      ),
    unwatch: (repository) =>
      setWatched((prev) => prev.filter((scope) => scope !== repository)),
    joinRoom: (target, after) => rooms.current?.join(target, after),
    leaveRoom: (target) => rooms.current?.leave(target),
  }

  const receive = useEffectEvent((message: MessageEvent) => {
    const data: unknown = message.data
    if (
      !data ||
      typeof data !== "object" ||
      !("kind" in data) ||
      data.kind !== "request" ||
      !("id" in data) ||
      typeof data.id !== "number"
    )
      return
    const { id } = data
    handleBridgeRequest(
      host,
      "method" in data ? data.method : undefined,
      "params" in data ? data.params : undefined
    ).then(
      (result) => post({ kind: "response", id, result: result ?? null }),
      (error: unknown) => post({ kind: "response", id, error: errorOf(error) })
    )
  })

  const connect = () => {
    loads.current += 1
    if (loads.current > 1) {
      rooms.current?.closeAll()
      portRef.current?.close()
      portRef.current = null
      setEscaped(true)
      return
    }
    rooms.current = new PluginRooms(plugin.id, (event) => emit(event))
    const channel = new MessageChannel()
    portRef.current = channel.port1
    channel.port1.onmessage = receive
    channel.port1.start()
    frameRef.current?.contentWindow?.postMessage(
      { type: CONNECT_MESSAGE, version: PROTOCOL_VERSION },
      "*",
      [channel.port2]
    )
    emit({ type: "context", context: context() })
  }

  useEffect(
    () => () => {
      rooms.current?.closeAll()
      portRef.current?.close()
      portRef.current = null
    },
    []
  )

  const announceSelf = useEffectEvent(() =>
    emit({ type: "context", context: context() })
  )
  useEffect(() => {
    if (me.data) announceSelf()
  }, [me.data])

  useEffect(() => {
    emit({ type: "theme", theme })
  }, [theme])

  useEffect(() => {
    if (path === sentPath.current) return
    sentPath.current = path
    emit({ type: "path", path })
  }, [path])

  const userId = session.data?.user_id
  const watches = useQueries({
    queries: watched.map((repository) => ({
      queryKey: ["plugin-watch", plugin.id, repository] as const,
      queryFn: () => Date.now(),
      staleTime: Infinity,
      meta: {
        invalidatedBy: [
          invalidationTopic(
            "plugin-documents",
            repository === null
              ? `${plugin.plugin_id}/user/${userId}`
              : `${plugin.plugin_id}/${repository}`
          ),
        ],
      },
    })),
  })
  const seen = useRef(new Map<string | null, number>())
  const versions = watches.map((watch) => watch.dataUpdatedAt).join(",")
  const announceChanges = useEffectEvent(() => {
    watched.forEach((repository, index) => {
      const version = watches[index]?.dataUpdatedAt ?? 0
      const before = seen.current.get(repository)
      seen.current.set(repository, version)
      if (before !== undefined && before !== version)
        emit({ type: "storage.changed", repository })
    })
  })
  useEffect(() => announceChanges(), [versions])

  if (escaped)
    return (
      <EmptyState
        className="m-auto"
        icon={WarningCircleIcon}
        title={`${plugin.manifest.displayName} tried to leave its sandbox`}
        description="The plugin navigated its frame to another page, so it was stopped. Reload it to start it again."
        action={
          <Button
            size="sm"
            variant="outlined"
            onClick={() => {
              loads.current = 0
              setEscaped(false)
              setGeneration((value) => value + 1)
            }}
          >
            Reload plugin
          </Button>
        }
      />
    )

  return (
    <SandboxedHtmlFrame
      key={generation}
      ref={frameRef}
      testId="plugin-frame"
      title={plugin.manifest.displayName}
      html={srcDoc}
      sandbox={PLUGIN_SANDBOX}
      allow={PLUGIN_ALLOW}
      onLoad={connect}
      className="h-full min-h-0 flex-1"
    />
  )
}
