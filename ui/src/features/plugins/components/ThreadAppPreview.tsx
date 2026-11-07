import { Button } from "@langchain/macaw-components/Button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ShieldCheckIcon } from "@phosphor-icons/react/dist/ssr/ShieldCheck"
import { useNavigate } from "@tanstack/react-router"
import { useEffect, useState } from "react"
import { toast } from "sonner"

import { useThreadRunConfig } from "@/features/agents/lib/threadRunConfig"
import type { ProvidedTools } from "@/features/plugins/components/PluginAgentDock"
import {
  AgentPausedBanner,
  usePluginAgent,
  usePluginChat,
} from "@/features/plugins/components/PluginAgentDock"
import { PermissionList } from "@/features/plugins/components/PluginsSettings"
import { PluginView } from "@/features/plugins/components/PluginView"
import type { AppVersion, InstalledPlugin } from "@/features/plugins/lib/api"
import { useArmed } from "@/features/plugins/lib/approval"
import type { SharedContext } from "@/features/plugins/lib/bridge"
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
      {running ? (
        <RunningApp
          key={running.id}
          threadId={threadId}
          plugin={running}
          waiting={waiting}
          active={active}
        />
      ) : waiting ? (
        <div className="flex flex-col gap-space-3 p-space-4">
          <p className="text-xs text-primary">
            The agent&apos;s newest build of {waiting.manifest.displayName} asks
            for:
          </p>
          <PermissionList permissions={waiting.manifest.permissions} />
          <div>
            <ApproveBuild threadId={threadId} version={waiting} />
          </div>
        </div>
      ) : (
        <p className="p-space-4 text-xs text-secondary">
          The agent has not previewed an app here yet.
        </p>
      )}
    </section>
  )
}

/** Runs `version`, once it has been on screen a moment, so a click aimed elsewhere cannot approve it. */
function ApproveBuild({
  threadId,
  version,
}: {
  threadId: string
  version: AppVersion
}) {
  const approve = useApprovePreview(threadId)
  const armed = useArmed(version.id)
  return (
    <Button
      size="xs"
      disabled={!armed}
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
  )
}

/**
 * A newer build waiting for permissions while the app runs: a button in the
 * header, which keeps the running frame where it is, opening the approval in
 * a dialog the frame cannot cover.
 */
function WaitingBuild({
  threadId,
  version,
}: {
  threadId: string
  version: AppVersion
}) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button size="xs" onClick={() => setOpen(true)}>
        Review build #{version.number}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          childrenClassName="gap-space-4 p-space-6"
          showClose={false}
        >
          <DialogTitle>
            Run build #{version.number} of {version.manifest.displayName}?
          </DialogTitle>
          <DialogDescription>
            The agent&apos;s newest build asks for:
          </DialogDescription>
          <PermissionList permissions={version.manifest.permissions} />
          <div className="flex justify-end gap-space-2">
            <Button size="xs" variant="outlined" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <ApproveBuild threadId={threadId} version={version} />
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

function RunningApp({
  threadId,
  plugin,
  waiting,
  active,
}: {
  threadId: string
  plugin: InstalledPlugin
  waiting: AppVersion | null
  active: boolean
}) {
  const [path, setPath] = useState("/")
  const agent = usePluginAgent()
  const release = useReleasePreview(threadId)
  const navigate = useNavigate()

  return (
    <>
      <header className="flex h-9 shrink-0 items-center gap-space-2 border-b border-muted px-space-3 text-xs">
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
        <div className="ml-auto flex shrink-0 items-center gap-space-2">
          {waiting && <WaitingBuild threadId={threadId} version={waiting} />}
          <Button
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
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col">
        <PluginView
          plugin={plugin}
          path={path}
          active={active}
          onPath={(next) => setPath(next)}
          setTitle={() => {}}
          agent={{ ...agent, chooseChat: () => {}, openChat: () => {} }}
        />
      </div>
      {plugin.manifest.permissions.includes("agent") && (
        <PreviewChat
          threadId={threadId}
          plugin={plugin}
          tools={agent.tools}
          context={agent.context}
        />
      )}
    </>
  )
}

// The chat beside a preview is the thread building it, whatever the name.
const PREVIEW_CHAT = { name: "default", repository: null }

/**
 * Answers the agent's calls to the preview's tools from its frame, and gives
 * the thread's messages its tools and context. Below the frame, so the notice
 * never moves it.
 */
function PreviewChat({
  threadId,
  plugin,
  tools,
  context,
}: {
  threadId: string
  plugin: InstalledPlugin
  tools: ProvidedTools | null
  context: SharedContext | null
}) {
  const { answering, run } = usePluginChat(plugin, PREVIEW_CHAT, tools, context)
  const setRunConfig = useThreadRunConfig((state) => state.set)
  useEffect(() => {
    setRunConfig(threadId, run)
    return () => setRunConfig(threadId, null)
  }, [run, setRunConfig, threadId])
  if (!answering.paused) return null
  return (
    <AgentPausedBanner
      name={plugin.manifest.displayName}
      onDismiss={answering.dismiss}
      className="m-space-3"
    />
  )
}
