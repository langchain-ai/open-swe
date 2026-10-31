import { ErrorState } from "@langchain/macaw-components/ErrorState"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useNavigate } from "@tanstack/react-router"
import { useEffectEvent } from "react"

import { ApprovalDialog } from "@/features/plugins/components/ApprovalDialog"
import { PluginFrame } from "@/features/plugins/components/PluginFrame"
import type { InstalledPlugin } from "@/features/plugins/lib/api"
import { useApproval } from "@/features/plugins/lib/approval"
import type {
  AgentToolOffer,
  ChatTarget,
  SharedContext,
  ToolRunner,
} from "@/features/plugins/lib/bridge"
import {
  usePluginBundle,
  useSharePluginRepositories,
} from "@/features/plugins/lib/queries"

/** What a plugin's frame may ask of the agent beside it. */
export interface PluginAgentHooks {
  provideTools: (offer: AgentToolOffer, run: ToolRunner) => () => void
  chooseChat: (chat: ChatTarget) => void
  openChat: (text: string | null) => void
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
      <PluginFrame
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
          onScreen() && (await approval.ask({ kind: "link", url })) !== null
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
