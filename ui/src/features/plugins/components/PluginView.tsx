import { ErrorState } from "@langchain/macaw-components/ErrorState"
import { useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useEffectEvent } from "react"
import { toast } from "sonner"

import { Skeleton } from "@/components/Skeleton"
import { ApprovalDialog } from "@/features/plugins/components/ApprovalDialog"
import { PluginFrame } from "@/features/plugins/components/PluginFrame"
import {
  pluginsApi,
  type AppVersion,
  type InstalledPlugin,
} from "@/features/plugins/lib/api"
import { useApproval } from "@/features/plugins/lib/approval"
import type {
  AgentToolOffer,
  ChatTarget,
  SharedContext,
  ToolRunner,
} from "@/features/plugins/lib/bridge"
import {
  pluginKeys,
  usePluginBundle,
  useSharePluginRepositories,
  useUpgradePlugin,
} from "@/features/plugins/lib/queries"
import type { ImageChunk } from "@/features/agents/lib/types"

/** What a plugin's frame may ask of the agent beside it. */
export interface PluginAgentHooks {
  provideTools: (offer: AgentToolOffer, run: ToolRunner) => () => void
  chooseChat: (chat: ChatTarget) => void
  openChat: (text: string | null, images: Array<ImageChunk>) => void
  shareContext: (context: SharedContext | null) => void
}

/**
 * One installed plugin running in its frame, wherever it is shown: its own
 * page, or beside the thread whose agent builds it. `active` is whether it is
 * on screen; a hidden plugin can neither move the web UI nor ask the person
 * anything.
 */
export function PluginView({
  plugin,
  path,
  active,
  onPath,
  setTitle,
  agent,
}: {
  plugin: InstalledPlugin
  path: string
  active: boolean
  /** Hears every path the plugin moves to, on screen or not. */
  onPath: (path: string, replace: boolean) => void
  setTitle: (title: string | null) => void
  agent: PluginAgentHooks
}) {
  const navigate = useNavigate()
  const onScreen = useEffectEvent(() => active)
  const bundle = usePluginBundle(plugin)
  const approval = useApproval()
  const share = useSharePluginRepositories()
  const upgrade = useUpgradePlugin()
  const queryClient = useQueryClient()

  /** The app's newest release, when it's newer than the one running. */
  const newer = async (): Promise<AppVersion | null> => {
    const apps = await queryClient.fetchQuery({
      queryKey: pluginKeys.apps,
      queryFn: pluginsApi.apps,
      staleTime: 60_000,
    })
    const latest = apps.find((app) => app.id === plugin.plugin_id)?.latest
    return latest && latest.number > plugin.version.number ? latest : null
  }
  const added = (release: AppVersion) =>
    release.manifest.permissions.filter(
      (permission) => !plugin.manifest.permissions.includes(permission)
    )

  if (bundle.error)
    return (
      <div className="flex flex-1 items-center justify-center p-space-5">
        <ErrorState
          title={`Couldn't load ${plugin.manifest.displayName}`}
          message={bundle.error.message}
          action={null}
        />
      </div>
    )
  if (bundle.data === undefined)
    return <Skeleton className="m-space-5 flex-1" />
  return (
    <>
      {/* A new version is a new frame: the one it replaces counts a second load as an escape. */}
      <PluginFrame
        key={plugin.version.id}
        plugin={plugin}
        html={bundle.data}
        path={path}
        navigate={onPath}
        openThread={(threadId) => {
          if (onScreen())
            void navigate({ to: "/agents/$threadId", params: { threadId } })
        }}
        setTitle={setTitle}
        visible={onScreen}
        confirmLink={async (url) =>
          onScreen() &&
          (new URL(url).hostname === "github.com" ||
            (await approval.ask({ kind: "link", url })) !== null)
        }
        pickRepositories={async (pick) => {
          if (!onScreen()) return []
          const chosen = await approval.ask({ kind: "repositories", pick })
          if (!Array.isArray(chosen) || chosen.length === 0) return []
          const updated = await share.mutateAsync({
            id: plugin.id,
            repositories: [...chosen],
          })
          return chosen.filter((repo) => updated.repositories.includes(repo))
        }}
        newerRelease={async () => {
          const release = await newer()
          return (
            release && {
              number: release.number,
              version: release.manifest.version,
              newPermissions: added(release),
            }
          )
        }}
        updateApp={async () => {
          if (!onScreen()) return false
          const release = await newer()
          if (!release) return false
          const answer = await approval.ask({
            kind: "update",
            release,
            newPermissions: added(release),
          })
          if (answer === null) return false
          try {
            await upgrade.mutateAsync({
              id: plugin.id,
              version: release.id,
              permissions: release.manifest.permissions,
            })
          } catch (error) {
            toast.error(`Couldn't update ${plugin.manifest.displayName}`, {
              description: error instanceof Error ? error.message : undefined,
            })
            return false
          }
          toast.success(
            `Updated ${plugin.manifest.displayName} to #${release.number}`
          )
          return true
        }}
        provideTools={agent.provideTools}
        chooseChat={agent.chooseChat}
        openChat={agent.openChat}
        shareContext={agent.shareContext}
      />
      <ApprovalDialog
        plugin={plugin}
        approval={approval.shown}
        armed={approval.armed}
        onAnswer={approval.answer}
      />
    </>
  )
}
