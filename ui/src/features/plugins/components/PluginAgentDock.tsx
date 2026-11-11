import { Banner } from "@langchain/macaw-components/Banner"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useQuery } from "@tanstack/react-query"
import {
  useEffect,
  useEffectEvent,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"
import { toast } from "sonner"

import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { EmbeddedThreadContext } from "@/features/agents/lib/embeddedThread"
import type { ThreadRun } from "@/features/agents/lib/threadRunConfig"
import type { ComposerDraft } from "@/features/agents/lib/types"
import type { InstalledPlugin } from "@/features/plugins/lib/api"
import { pluginsApi } from "@/features/plugins/lib/api"
import { PluginChatDeliveries } from "@/features/plugins/lib/chatDelivery"
import type {
  AgentToolOffer,
  ChatTarget,
  SharedContext,
  ToolRunner,
} from "@/features/plugins/lib/bridge"
import { invalidationTopic } from "@/lib/invalidations/topics"

/** What the plugin's frame offers the agent right now, and how to reach it. */
export interface ProvidedTools {
  offer: AgentToolOffer
  run: ToolRunner
}

const NO_TOOLS: AgentToolOffer = { tools: [], instructions: "" }

/**
 * What a plugin's frame gives the agent beside it: the tools it offers now
 * and what it says the person has open. Each offer replaces the one before,
 * so withdrawing one already replaced changes nothing.
 */
export function usePluginAgent() {
  const [tools, setTools] = useState<ProvidedTools | null>(null)
  const [context, setContext] = useState<SharedContext | null>(null)
  const latest = useRef<symbol | null>(null)
  return {
    tools,
    context,
    shareContext: setContext,
    provideTools: (offer: AgentToolOffer, run: ToolRunner) => {
      const offered = Symbol("offer")
      latest.current = offered
      setTools({ offer, run })
      return () => {
        if (latest.current !== offered) return
        latest.current = null
        setTools(null)
      }
    },
  }
}

/** Why the agent stopped calling the plugin's tools, and how to let it go on. */
export function AgentPausedBanner({
  name,
  onDismiss,
  className,
}: {
  name: string
  onDismiss: () => void
  className?: string
}) {
  return (
    <Banner intent="neutral" className={className} onDismiss={onDismiss}>
      The agent paused after many calls to {name}&apos;s tools. Send a message
      to let it go on.
    </Banner>
  )
}

/**
 * The agent a plugin works with, docked beside it: the person's chat under the
 * name the plugin chose, in the normal thread view. When the agent calls one
 * of the plugin's tools, the call runs in the plugin's frame and the thread
 * resumes with what it returned.
 */
export function PluginAgentDock({
  plugin,
  chat,
  draft,
  tools,
  context,
  active,
  onThread,
}: {
  plugin: InstalledPlugin
  chat: ChatTarget
  draft: ComposerDraft | undefined
  /** `null` until the plugin's frame offers any. */
  tools: ProvidedTools | null
  /** What the plugin last said the person has open; each message carries it. */
  context: SharedContext | null
  /** Whether the plugin's tab is on screen; a hidden one keeps its thread but stops streaming. */
  active: boolean
  /** Hears the chat's thread once it is open, for the bar above to link to. */
  onThread: (threadId: string) => void
}) {
  const { opened, threadId, answering, run } = usePluginChat(
    plugin,
    chat,
    tools,
    context
  )
  const reportThread = useEffectEvent((id: string) => onThread(id))
  useEffect(() => {
    if (threadId) reportThread(threadId)
  }, [threadId])
  const embedded = {
    run,
    emptyState: (
      <p className="max-w-xs text-center text-xs text-tertiary">
        Ask the agent about what you have open in {plugin.manifest.displayName}.
        It works as you, with the repository and the tools{" "}
        {plugin.manifest.displayName} gives it.
      </p>
    ),
  }

  return (
    <section
      aria-label={`Agent for ${plugin.manifest.displayName}`}
      className="flex h-full min-h-0 flex-col"
    >
      {opened.isError ? (
        <p className="p-space-4 text-xs text-secondary">
          The agent isn&apos;t available: {opened.error.message}
        </p>
      ) : threadId ? (
        <EmbeddedThreadContext.Provider value={embedded}>
          {answering.paused && (
            <AgentPausedBanner
              name={plugin.manifest.displayName}
              onDismiss={answering.dismiss}
              className="m-space-3 mb-0"
            />
          )}
          <div className="flex min-h-0 flex-1 flex-col [&>*]:min-h-0 [&>*]:flex-1">
            <AgentThreadPage
              key={threadId}
              threadId={threadId}
              active={active}
              composerDraft={draft}
              ownsTitle={false}
            />
          </div>
        </EmbeddedThreadContext.Provider>
      ) : (
        <div className="flex flex-1 flex-col justify-end gap-space-3 p-space-4">
          <Skeleton className="h-16 w-3/4" />
          <Skeleton className="ml-auto h-10 w-1/2" />
        </div>
      )}
    </section>
  )
}

/**
 * The person's chat under ``chat`` beside the plugin, with the tools its frame
 * offers: the agent's calls to them run in the frame and the thread resumes
 * with what they returned. `run` is what each message sent with the tools
 * carries.
 */
export function usePluginChat(
  plugin: InstalledPlugin,
  chat: ChatTarget,
  tools: ProvidedTools | null,
  context: SharedContext | null
) {
  const opened = useQuery({
    queryKey: ["plugin-chat", plugin.id, chat],
    queryFn: () => pluginsApi.openChat(plugin.id, chat),
    staleTime: Infinity,
  })
  const chatId = opened.data?.chat_id
  const threadId = opened.data?.thread_id
  const offer = tools?.offer
  useToolOffer(plugin, chatId, offer)
  const answering = useAnsweredCalls(plugin, chatId, threadId, tools)
  // Kept stable: the thread beside a preview holds it in an effect.
  const run = useMemo(
    (): ThreadRun => ({
      // The backend gives the plugin's tools only to runs sent with
      // `plugin_dock`, and puts what the plugin has open ahead of each message.
      configurable: {
        plugin_dock: true,
        ...(context ? { plugin_context: context.json } : {}),
        ...(context?.label ? { plugin_context_label: context.label } : {}),
      },
      // The chat holds one offer, the last any dock on it sent, so each sends its own first.
      prepare: () =>
        chatId && offer ? sendOffer(plugin, chatId, offer) : Promise.resolve(),
    }),
    [chatId, context, offer, plugin]
  )
  return { opened, threadId, answering, run }
}

// Offers to one chat go out in order, so an older offer never lands after a newer one.
const offers = new Map<string, Promise<void>>()

function sendOffer(
  plugin: InstalledPlugin,
  chatId: string,
  offer: AgentToolOffer
): Promise<void> {
  const sent = (offers.get(chatId) ?? Promise.resolve())
    .then(() => pluginsApi.offerTools(plugin.id, chatId, offer))
    .catch((error: unknown) => {
      toast.error(
        `Couldn't give the agent ${plugin.manifest.displayName}'s tools`,
        { description: error instanceof Error ? error.message : undefined }
      )
    })
  offers.set(chatId, sent)
  return sent
}

/**
 * Gives the chat what the plugin's frame offers whenever that changes, a
 * withdrawal included. Every dock on the chat shares its one offer, so a dock
 * whose frame has offered nothing yet, or that closes, leaves another's be:
 * only runs sent from a dock, which offers its own first, and the runs its
 * answers resume get the plugin's tools.
 */
function useToolOffer(
  plugin: InstalledPlugin,
  chatId: string | undefined,
  offer: AgentToolOffer | undefined
) {
  const json = offer ? JSON.stringify(offer) : null
  const current = useEffectEvent(() => plugin)
  const offeredTo = useRef<string | null>(null)
  useEffect(() => {
    if (!chatId || (json === null && offeredTo.current !== chatId)) return
    offeredTo.current = chatId
    void sendOffer(
      current(),
      chatId,
      json === null ? NO_TOOLS : (JSON.parse(json) as AgentToolOffer)
    )
  }, [chatId, json])
}

/**
 * Runs the calls the agent made to the plugin's tools in its frame and
 * resumes the thread with their results. The dock claims calls first, so a
 * second open dock never runs them too, and runs them in order; a result it
 * could not deliver is sent again rather than run again. Calls wait while the
 * frame has offered nothing yet, as it loads.
 */
function useAnsweredCalls(
  plugin: InstalledPlugin,
  chatId: string | undefined,
  threadId: string | undefined,
  tools: ProvidedTools | null
) {
  const calls = useQuery({
    queryKey: ["plugin-chat-calls", plugin.id, chatId],
    queryFn: () => pluginsApi.chatCalls(plugin.id, chatId!),
    enabled: Boolean(chatId && threadId),
    meta: {
      invalidatedBy: threadId
        ? [invalidationTopic("plugin-chat-calls", threadId)]
        : [],
    },
  })
  const [deliveries] = useState(() => new PluginChatDeliveries(plugin.id))
  const paused = useSyncExternalStore(
    (listener) => deliveries.subscribe(listener),
    () => (chatId ? deliveries.isPaused(chatId) : false),
    () => false
  )
  const [retries, setRetries] = useState(0)
  const mounted = useRef(false)
  const retry = useRef<ReturnType<typeof setTimeout> | null>(null)
  const name = plugin.manifest.displayName

  const answer = useEffectEvent(async () => {
    if (!chatId || !tools) return
    let failed = false
    try {
      await deliveries.answer(chatId, tools, name)
    } catch (error) {
      failed = true
      toast.error(`Couldn't send ${name}'s answer to the agent`, {
        description: error instanceof Error ? error.message : undefined,
      })
    }
    // New calls arrive by invalidation; a timer only retries what failed.
    if (mounted.current && (failed || deliveries.hasUndelivered(chatId))) {
      if (retry.current) clearTimeout(retry.current)
      retry.current = setTimeout(() => setRetries((n) => n + 1), 5_000)
    }
  })
  useEffect(() => {
    if (chatId && (calls.data?.length || deliveries.hasUndelivered(chatId)))
      void answer()
  }, [chatId, calls.data, tools, retries, deliveries])
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (retry.current) clearTimeout(retry.current)
    }
  }, [])
  return {
    paused,
    dismiss: () => {
      if (chatId) deliveries.dismiss(chatId)
    },
  }
}
