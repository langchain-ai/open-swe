import { Button } from "@langchain/macaw-components/Button"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ShieldCheckIcon } from "@phosphor-icons/react/dist/ssr/ShieldCheck"
import { useNavigate } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { useThreadRunConfig } from "@/features/agents/lib/threadRunConfig"
import type { ProvidedTools } from "@/features/plugins/components/PluginAgentDock"
import { usePluginChat } from "@/features/plugins/components/PluginAgentDock"
import { PermissionList } from "@/features/plugins/components/PluginsSettings"
import { PluginView } from "@/features/plugins/components/PluginView"
import type { AppVersion, InstalledPlugin } from "@/features/plugins/lib/api"
import type {
  AgentToolOffer,
  SharedContext,
  ToolRunner,
} from "@/features/plugins/lib/bridge"
import { PluginIcon } from "@/features/plugins/lib/icons"
import {
  useApprovePreview,
  useAppPreview,
  useReleasePreview,
} from "@/features/plugins/lib/queries"

/**
 * The app the thread's agent is building, running beside the thread. Each
 * preview the agent makes replaces it; the person chats about it in the
 * thread itself, whose messages carry the app's tools and context while it is
 * open here.
 */
export function ThreadAppPreview({
  threadId,
  active,
}: {
  threadId: string
  active: boolean
}) {
  const preview = useAppPreview(threadId)
  if (preview.isPending) return <Skeleton className="m-space-4 flex-1" />
  if (preview.error)
    return (
      <p className="p-space-4 text-xs text-secondary">
        Couldn&apos;t load the app: {preview.error.message}
      </p>
    )
  const { running, waiting } = preview.data
  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label="App preview">
      {waiting && <WaitingBuild threadId={threadId} version={waiting} />}
      {running ? (
        <RunningApp
          key={running.id}
          threadId={threadId}
          plugin={running}
          active={active}
        />
      ) : (
        !waiting && (
          <p className="p-space-4 text-xs text-secondary">
            The agent has not previewed an app here yet.
          </p>
        )
      )}
    </section>
  )
}

function WaitingBuild({
  threadId,
  version,
}: {
  threadId: string
  version: AppVersion
}) {
  const approve = useApprovePreview(threadId)
  return (
    <div className="flex flex-col gap-space-3 border-b border-default p-space-4">
      <p className="text-xs text-primary">
        The agent&apos;s newest build of {version.manifest.displayName} asks
        for:
      </p>
      <PermissionList permissions={version.manifest.permissions} />
      <div>
        <Button
          size="xs"
          loading={approve.isPending}
          onClick={() =>
            approve.mutate({
              version: version.id,
              permissions: version.manifest.permissions,
            })
          }
        >
          Approve and run it
        </Button>
      </div>
    </div>
  )
}

function RunningApp({
  threadId,
  plugin,
  active,
}: {
  threadId: string
  plugin: InstalledPlugin
  active: boolean
}) {
  const [path, setPath] = useState("/")
  const [tools, setTools] = useState<ProvidedTools | null>(null)
  const [context, setContext] = useState<SharedContext | null>(null)
  const latest = useRef<symbol | null>(null)
  const [paused, setPaused] = useState(false)
  const release = useReleasePreview(threadId)
  const navigate = useNavigate()
  const usesAgent = plugin.manifest.permissions.includes("agent")

  const setRunConfig = useThreadRunConfig((state) => state.set)
  useEffect(() => {
    if (!usesAgent) return
    setRunConfig(threadId, {
      plugin_dock: true,
      ...(context ? { plugin_context: context.json } : {}),
      ...(context?.label ? { plugin_context_label: context.label } : {}),
    })
    return () => setRunConfig(threadId, null)
  }, [context, setRunConfig, threadId, usesAgent])

  return (
    <>
      <header className="flex h-9 shrink-0 items-center gap-space-2 border-b border-default px-space-3 text-xs">
        <PluginIcon
          icon={plugin.manifest.sidebar.icon}
          size={14}
          className="shrink-0 text-icon-secondary"
        />
        <span className="truncate font-medium text-primary">
          {plugin.manifest.displayName}
        </span>
        <span className="flex shrink-0 items-center gap-space-1 text-secondary">
          <ShieldCheckIcon size={12} />
          Preview #{plugin.version.number}
        </span>
        <Button
          className="ml-auto"
          size="xs"
          variant="outlined"
          loading={release.isPending}
          onClick={() =>
            release.mutate(undefined, {
              onSuccess: (installed) =>
                toast.success(
                  `${installed.manifest.displayName} #${installed.version.number} is in your sidebar`,
                  {
                    action: {
                      label: "Open",
                      onClick: () =>
                        void navigate({
                          to: "/agents/plugins/$name/$",
                          params: { name: installed.name, _splat: "" },
                        }),
                    },
                  }
                ),
            })
          }
        >
          Add to sidebar
        </Button>
      </header>
      {usesAgent && (
        <PreviewChat plugin={plugin} tools={tools} onPaused={setPaused} />
      )}
      {paused && (
        <p className="border-b border-default px-space-3 py-space-2 text-xs text-secondary">
          The agent paused after many calls to {plugin.manifest.displayName}
          &apos;s tools. Send a message to let it go on.
        </p>
      )}
      <div className="flex min-h-0 flex-1 flex-col">
        <PluginView
          plugin={plugin}
          path={path}
          active={active}
          onPath={(next) => setPath(next)}
          setTitle={() => {}}
          agent={{
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
            chooseChat: () => {},
            openChat: () => {},
            shareContext: setContext,
          }}
        />
      </div>
    </>
  )
}

// The chat beside a preview is the thread building it, whatever the name.
const PREVIEW_CHAT = { name: "default", repository: null }

/** Answers the agent's calls to the preview's tools from its frame. */
function PreviewChat({
  plugin,
  tools,
  onPaused,
}: {
  plugin: InstalledPlugin
  tools: ProvidedTools | null
  onPaused: (paused: boolean) => void
}) {
  const { answering } = usePluginChat(plugin, PREVIEW_CHAT, tools)
  useEffect(() => onPaused(answering.paused), [answering.paused, onPaused])
  return null
}
