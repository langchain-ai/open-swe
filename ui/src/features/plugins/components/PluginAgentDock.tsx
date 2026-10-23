import { Banner } from "@langchain/macaw-components/Banner"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ArrowsOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowsOut"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { EmbeddedThreadContext } from "@/features/agents/lib/embeddedThread"
import type { InstalledPlugin } from "@/features/plugins/lib/api"
import { pluginsApi } from "@/features/plugins/lib/api"
import type {
  AgentToolOffer,
  ChatTarget,
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
  active,
  onClose,
}: {
  plugin: InstalledPlugin
  chat: ChatTarget
  draft: { key: number; text: string } | undefined
  /** `null` until the plugin's frame offers any. */
  tools: ProvidedTools | null
  /** Whether the plugin's tab is on screen; a hidden one keeps its thread but stops streaming. */
  active: boolean
  onClose: () => void
}) {
  const opened = useQuery({
    queryKey: ["plugin-chat", plugin.id, chat],
    queryFn: () => pluginsApi.openChat(plugin.id, chat),
    staleTime: Infinity,
  })
  const chatId = opened.data?.chat_id
  const threadId = opened.data?.thread_id
  useToolOffer(plugin, chatId, tools?.offer ?? NO_TOOLS)
  const answering = useAnsweredCalls(plugin, chatId, threadId, tools)
  const embedded = useMemo(
    () => ({
      emptyState: (
        <p className="max-w-xs text-center text-xs text-tertiary">
          Ask the agent about what you have open in{" "}
          {plugin.manifest.displayName}. It works as you, with the repository
          and the tools {plugin.manifest.displayName} gives it.
        </p>
      ),
    }),
    [plugin.manifest.displayName]
  )

  return (
    <section
      aria-label={`Agent for ${plugin.manifest.displayName}`}
      className="flex h-full min-h-0 flex-col"
    >
      <header className="flex h-10 shrink-0 items-center gap-space-1 border-b border-default px-space-3 text-xs">
        <span className="min-w-0 flex-1 truncate font-medium text-primary">
          Agent
        </span>
        {threadId && (
          <Link
            to="/agents/$threadId"
            params={{ threadId }}
            aria-label="Open as a full thread"
            title="Open as a full thread"
            className="inline-flex size-6 items-center justify-center rounded-md text-icon-secondary hover:bg-surface-level-1-hover"
          >
            <ArrowsOutIcon size={14} />
          </Link>
        )}
        <IconButton
          icon={XIcon}
          label="Close the agent"
          size="xs"
          variant="plain"
          color="secondary"
          onClick={onClose}
        />
      </header>
      {opened.isError ? (
        <p className="p-space-4 text-xs text-secondary">
          The agent isn&apos;t available: {opened.error.message}
        </p>
      ) : threadId ? (
        <EmbeddedThreadContext.Provider value={embedded}>
          {answering.paused && (
            <Banner
              intent="neutral"
              className="m-space-3 mb-0"
              onDismiss={answering.dismiss}
            >
              The agent paused after many calls to {plugin.manifest.displayName}
              &apos;s tools. Send a message to let it go on.
            </Banner>
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

// Offers to one chat go out in order, so withdrawing on close never lands after a reopen's offer.
const offers = new Map<string, Promise<void>>()

function sendOffer(
  plugin: InstalledPlugin,
  chatId: string,
  offer: AgentToolOffer
) {
  const sent = (offers.get(chatId) ?? Promise.resolve())
    .then(() => pluginsApi.offerTools(plugin.id, chatId, offer))
    .catch((error: unknown) => {
      toast.error(
        `Couldn't give the agent ${plugin.manifest.displayName}'s tools`,
        { description: error instanceof Error ? error.message : undefined }
      )
    })
  offers.set(chatId, sent)
}

/**
 * Gives the chat's next runs what the plugin offers now, and takes it back
 * when the dock closes, so a run started elsewhere never waits on a tool
 * nothing here will answer.
 */
function useToolOffer(
  plugin: InstalledPlugin,
  chatId: string | undefined,
  offer: AgentToolOffer
) {
  const json = JSON.stringify(offer)
  const current = useEffectEvent(() => plugin)
  useEffect(() => {
    if (chatId) sendOffer(current(), chatId, JSON.parse(json) as AgentToolOffer)
  }, [chatId, json])
  useEffect(() => {
    if (!chatId) return
    return () => sendOffer(current(), chatId, NO_TOOLS)
  }, [chatId])
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
  const [paused, setPaused] = useState(false)
  const [retries, setRetries] = useState(0)
  const busy = useRef(false)
  const undelivered = useRef(new Map<string, string>())
  const retry = useRef<ReturnType<typeof setTimeout> | null>(null)
  const name = plugin.manifest.displayName

  const answer = useEffectEvent(async () => {
    if (!chatId || !tools || busy.current) return
    busy.current = true
    try {
      const offered = new Set(tools.offer.tools.map((tool) => tool.name))
      for (const call of await pluginsApi.claimCalls(plugin.id, chatId)) {
        if (undelivered.current.has(call.call_id)) continue
        const output = offered.has(call.tool)
          ? await tools
              .run(call.tool, call.arguments)
              .catch(
                (error: unknown) =>
                  `Error: ${error instanceof Error ? error.message : String(error)}`
              )
          : `Error: ${name} does not offer ${call.tool} now.`
        undelivered.current.set(call.call_id, output)
      }
      const outputs = [...undelivered.current].map(([call_id, output]) => ({
        call_id,
        output,
      }))
      if (outputs.length === 0) return
      const sent = await pluginsApi.answerCalls(plugin.id, chatId, outputs)
      undelivered.current.clear()
      setPaused(sent.paused)
    } catch (error) {
      toast.error(`Couldn't send ${name}'s answer to the agent`, {
        description: error instanceof Error ? error.message : undefined,
      })
      retry.current = setTimeout(() => setRetries((n) => n + 1), 5_000)
    } finally {
      busy.current = false
    }
  })
  useEffect(() => {
    if (calls.data?.length || undelivered.current.size) void answer()
  }, [calls.data, tools, retries])
  useEffect(
    () => () => {
      if (retry.current) clearTimeout(retry.current)
    },
    []
  )
  return { paused, dismiss: () => setPaused(false) }
}
