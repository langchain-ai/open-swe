import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ArrowsOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowsOut"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useEffect, useEffectEvent, useMemo, useRef } from "react"
import { toast } from "sonner"

import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { EmbeddedThreadContext } from "@/features/agents/lib/embeddedThread"
import type { ChatCall, InstalledPlugin } from "@/features/plugins/lib/api"
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
  useAnsweredCalls(plugin, chatId, threadId, tools)
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

/** Gives the chat's next runs what the plugin offers now. */
function useToolOffer(
  plugin: InstalledPlugin,
  chatId: string | undefined,
  offer: AgentToolOffer
) {
  const json = JSON.stringify(offer)
  useEffect(() => {
    if (!chatId) return
    pluginsApi
      .offerTools(plugin.id, chatId, JSON.parse(json) as AgentToolOffer)
      .catch((error: unknown) =>
        toast.error(
          `Couldn't give the agent ${plugin.manifest.displayName}'s tools`,
          {
            description: error instanceof Error ? error.message : undefined,
          }
        )
      )
  }, [plugin.id, plugin.manifest.displayName, chatId, json])
}

/**
 * Runs the calls the agent made to the plugin's tools in its frame, each once,
 * and resumes the thread with their results. Calls wait while the frame has
 * offered nothing yet, as it loads.
 */
function useAnsweredCalls(
  plugin: InstalledPlugin,
  chatId: string | undefined,
  threadId: string | undefined,
  tools: ProvidedTools | null
) {
  const queryClient = useQueryClient()
  const key = ["plugin-chat-calls", plugin.id, chatId] as const
  const calls = useQuery({
    queryKey: key,
    queryFn: () => pluginsApi.chatCalls(plugin.id, chatId!),
    enabled: Boolean(chatId && threadId),
    meta: {
      invalidatedBy: threadId
        ? [invalidationTopic("plugin-chat-calls", threadId)]
        : [],
    },
  })
  const answering = useRef(new Set<string>())
  const answer = useEffectEvent((waiting: ReadonlyArray<ChatCall>) => {
    if (!chatId || !tools) return
    const fresh = waiting.filter((call) => !answering.current.has(call.call_id))
    if (fresh.length === 0) return
    for (const call of fresh) answering.current.add(call.call_id)
    const offered = new Set(tools.offer.tools.map((tool) => tool.name))
    void Promise.all(
      fresh.map(async (call) => ({
        call_id: call.call_id,
        output: offered.has(call.tool)
          ? await tools
              .run(call.tool, call.arguments)
              .catch(
                (error: unknown) =>
                  `Error: ${error instanceof Error ? error.message : String(error)}`
              )
          : `Error: ${plugin.manifest.displayName} does not offer ${call.tool} now.`,
      }))
    )
      .then((outputs) => pluginsApi.answerCalls(plugin.id, chatId, outputs))
      .then(() => queryClient.invalidateQueries({ queryKey: key }))
      .catch((error: unknown) => {
        for (const call of fresh) answering.current.delete(call.call_id)
        toast.error(
          `Couldn't send ${plugin.manifest.displayName}'s answer to the agent`,
          {
            description: error instanceof Error ? error.message : undefined,
          }
        )
      })
  })
  useEffect(() => {
    if (calls.data?.length) answer(calls.data)
  }, [calls.data, tools])
}
