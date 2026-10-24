import {
  CONNECT_MESSAGE,
  HELLO_MESSAGE,
  PROTOCOL_VERSION,
  type HostContext,
  type HostEvent,
  type Person,
} from "@openswe/plugin-sdk"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { useQueries, useQueryClient } from "@tanstack/react-query"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"
import { newMessagePortRpcSession } from "capnweb"
import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import type {
  DocumentTarget,
  InstalledPlugin,
} from "@/features/plugins/lib/api"
import { documentsTopicKey, pluginsApi } from "@/features/plugins/lib/api"
import { PluginRooms } from "@/features/plugins/lib/rooms"
import type {
  AgentToolOffer,
  BridgeHost,
  ChatTarget,
  RepositoryPick,
  SharedContext,
  ToolRunner,
} from "@/features/plugins/lib/bridge"
import {
  PluginHostApi,
  SessionBudget,
  sendableError,
} from "@/features/plugins/lib/bridge"
import {
  PLUGIN_ALLOW,
  ESCAPED_MESSAGE,
  PLUGIN_CSP,
  PLUGIN_SANDBOX,
  pluginShell,
} from "@/features/plugins/lib/shell"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { useIsInBaseOrg, useSession } from "@/lib/session"
import { useResolvedTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

/** One connected frame: its Cap'n Web session, its allowance and its live rooms. */
interface Connection {
  peer: Disposable
  budget: SessionBudget
  rooms: PluginRooms
}

interface DocumentWatch {
  target: DocumentTarget
  listeners: Set<() => void>
}

/**
 * One plugin's sandboxed frame and the Cap'n Web session it reaches the web UI
 * over.
 *
 * The frame is connected once. Should it load a second document, it navigated
 * itself away from the bundle, out from under its policy, so it is torn down
 * rather than handed a new port. Tearing down ends the session, which releases
 * every capability the plugin held and leaves every room it joined.
 */
export function PluginFrame({
  plugin,
  html,
  path,
  navigate,
  openThread,
  setTitle,
  visible,
  confirmLink,
  pickRepositories,
  provideTools,
  chooseChat,
  openChat,
  shareContext,
}: {
  plugin: InstalledPlugin
  html: string
  path: string
  navigate: (path: string, replace: boolean) => void
  openThread: (threadId: string) => void
  setTitle: (title: string | null) => void
  /** Whether the frame is on screen; a hidden one shows no toasts. */
  visible: () => boolean
  confirmLink: (url: string) => Promise<boolean>
  pickRepositories: (pick: RepositoryPick) => Promise<Array<string>>
  provideTools: (offer: AgentToolOffer, run: ToolRunner) => () => void
  chooseChat: (chat: ChatTarget) => void
  openChat: (draft: string | null) => void
  shareContext: (context: SharedContext) => void
}) {
  const session = useSession()
  const isInBaseOrg = useIsInBaseOrg()
  const theme = useResolvedTheme()
  const queryClient = useQueryClient()
  const frameRef = useRef<HTMLIFrameElement>(null)
  const connection = useRef<Connection | null>(null)
  const loads = useRef(0)
  const sentPath = useRef(path)
  const listeners = useRef(new Set<(event: HostEvent) => void>())
  const documentWatches = useRef(new Map<string, DocumentWatch>())
  const [generation, setGeneration] = useState(0)
  const [escaped, setEscaped] = useState(false)
  // A new frame paints a blank light page until its document loads; keep it
  // hidden until then so dark mode doesn't flash white.
  const [painted, setPainted] = useState(false)
  const [watched, setWatched] = useState<ReadonlyArray<DocumentTarget>>([])
  const [initialTheme] = useState(theme)
  const login = session.data?.login ?? ""
  const userId = session.data?.user_id ?? ""
  // A fresh nonce per mount: the web UI answers only the document it made.
  const [nonce, setNonce] = useState(() => crypto.randomUUID())
  const srcDoc = useMemo(
    () => pluginShell(html, initialTheme, nonce),
    [html, initialTheme, nonce]
  )

  const me = async (): Promise<Person> => {
    const found = login
      ? await queryClient.ensureQueryData({
          queryKey: ["plugin-person", plugin.id, login],
          queryFn: async () =>
            (await pluginsApi.people(plugin.id, [login]))[0] ?? null,
          staleTime: Infinity,
        })
      : null
    return found ?? { login, name: login, avatar: null }
  }

  const host: BridgeHost = {
    plugin,
    context: async (): Promise<HostContext> => ({
      protocol: PROTOCOL_VERSION,
      plugin: {
        id: plugin.id,
        name: plugin.name,
        displayName: plugin.manifest.displayName,
        version: plugin.manifest.version,
        permissions: plugin.manifest.permissions,
      },
      user: await me(),
      theme,
      path: sentPath.current,
    }),
    subscribe: (listener) => {
      listeners.current.add(listener)
      return () => listeners.current.delete(listener)
    },
    navigate: (next, replace) => {
      sentPath.current = next
      navigate(next, replace)
    },
    openThread,
    setTitle,
    toast: ({ title, description, intent }) => {
      if (!visible()) return
      const show =
        intent === "success"
          ? toast.success
          : intent === "error"
            ? toast.error
            : toast.info
      show(title, { description })
    },
    confirmLink,
    pickRepositories,
    isInBaseOrg,
    provideTools,
    chooseChat,
    openChat,
    shareContext,
    watchDocuments: (target, onChange) => {
      const key = documentsTopicKey(plugin.plugin_id, target, userId)
      let watch = documentWatches.current.get(key)
      if (!watch) {
        watch = { target, listeners: new Set() }
        documentWatches.current.set(key, watch)
        setWatched((prev) => [...prev, target])
      }
      const held = watch
      held.listeners.add(onChange)
      return () => {
        held.listeners.delete(onChange)
        if (held.listeners.size > 0) return
        documentWatches.current.delete(key)
        setWatched((prev) => prev.filter((other) => other !== held.target))
      }
    },
    joinRoom: (target, after, onEvent) =>
      connection.current?.rooms.join(target, after, onEvent) ?? (() => {}),
  }
  // The session outlives renders; it always reaches the latest props through this.
  const current = useEffectEvent(() => host)

  const teardown = () => {
    const held = connection.current
    connection.current = null
    if (!held) return
    held.budget.close()
    held.rooms.closeAll()
    held.peer[Symbol.dispose]()
    listeners.current.clear()
  }

  // A second document in the frame means it navigated away from the bundle.
  const loaded = () => {
    loads.current += 1
    setPainted(true)
    if (loads.current > 1) {
      teardown()
      setEscaped(true)
    }
  }

  /**
   * The port goes to the shell the web UI wrote, which can only hand it to the
   * plugin's own document: its `frame-src 'none'` keeps that frame from
   * loading anything else. The hello must come from the shell's window,
   * echoing this mount's nonce, and the port goes once.
   */
  const hello = useEffectEvent((message: MessageEvent) => {
    const data: unknown = message.data
    const frame = frameRef.current?.contentWindow
    if (
      !frame ||
      message.source !== frame ||
      message.origin !== "null" ||
      !data ||
      typeof data !== "object" ||
      !("type" in data) ||
      !("nonce" in data) ||
      data.nonce !== nonce
    )
      return
    if (data.type === ESCAPED_MESSAGE) {
      teardown()
      setEscaped(true)
      return
    }
    if (
      data.type !== HELLO_MESSAGE ||
      connection.current !== null ||
      loads.current > 1
    )
      return
    const budget = new SessionBudget()
    const channel = new MessageChannel()
    connection.current = {
      peer: newMessagePortRpcSession(
        channel.port1,
        new PluginHostApi(current, budget),
        { onSendError: sendableError, limits: { maxDepth: 64 } }
      ),
      budget,
      rooms: new PluginRooms(plugin.id),
    }
    frame.postMessage(
      { type: CONNECT_MESSAGE, version: PROTOCOL_VERSION },
      "*",
      [channel.port2]
    )
  })

  useEffect(() => {
    const listen = (message: MessageEvent) => hello(message)
    window.addEventListener("message", listen)
    return () => window.removeEventListener("message", listen)
  }, [])

  useEffect(() => teardown, [])

  const tell = useEffectEvent((event: HostEvent) => {
    for (const listener of listeners.current) listener(event)
  })

  useEffect(() => {
    tell({ type: "theme", theme })
  }, [theme])

  useEffect(() => {
    if (path === sentPath.current) return
    sentPath.current = path
    tell({ type: "path", path })
  }, [path])

  const watches = useQueries({
    queries: watched.map((target) => {
      const key = documentsTopicKey(plugin.plugin_id, target, userId)
      return {
        queryKey: ["plugin-watch", plugin.id, key] as const,
        queryFn: () => Date.now(),
        staleTime: Infinity,
        meta: { invalidatedBy: [invalidationTopic("plugin-documents", key)] },
      }
    }),
  })
  const seen = useRef(new Map<string, number>())
  const versions = watches.map((watch) => watch.dataUpdatedAt).join(",")
  const announceChanges = useEffectEvent(() => {
    watched.forEach((target, index) => {
      const key = documentsTopicKey(plugin.plugin_id, target, userId)
      const version = watches[index]?.dataUpdatedAt ?? 0
      const before = seen.current.get(key)
      seen.current.set(key, version)
      if (before !== undefined && before !== version)
        for (const onChange of documentWatches.current.get(key)?.listeners ??
          [])
          onChange()
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
              setPainted(false)
              setEscaped(false)
              setNonce(crypto.randomUUID())
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
      onLoad={loaded}
      csp={PLUGIN_CSP}
      className={cn("h-full min-h-0 flex-1", !painted && "invisible")}
    />
  )
}
