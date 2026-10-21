import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { ErrorState } from "@langchain/macaw-components/ErrorState"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { PuzzlePieceIcon } from "@phosphor-icons/react/dist/ssr/PuzzlePiece"
import { ShieldCheckIcon } from "@phosphor-icons/react/dist/ssr/ShieldCheck"
import { Link, useNavigate } from "@tanstack/react-router"
import { useEffect, useEffectEvent, useState } from "react"

import { PluginFrame } from "@/features/plugins/components/PluginFrame"
import { ApprovalPanel } from "@/features/plugins/components/ApprovalPanel"
import type { InstalledPlugin } from "@/features/plugins/lib/api"
import { useApproval } from "@/features/plugins/lib/approval"
import { PluginIcon } from "@/features/plugins/lib/icons"
import { pageTitle } from "@/lib/pageTitle"
import {
  usePlugin,
  usePluginBundle,
  useSharePluginRepositories,
} from "@/features/plugins/lib/queries"

/**
 * One installed plugin's page. `active` is whether it is the page on screen: a
 * plugin stays mounted while the person is elsewhere, like a tab, and while it
 * is hidden it can neither move the web UI nor ask the person anything.
 * `onPath` hears every path the plugin moves to, on screen or not.
 */
export function PluginPage({
  name,
  path,
  active,
  onPath,
}: {
  name: string
  path: string
  active: boolean
  onPath: (path: string) => void
}) {
  const { plugin, isPending, error } = usePlugin(name)
  if (isPending)
    return (
      <main className="flex min-w-0 flex-1 p-space-5">
        <Skeleton className="h-full w-full" />
      </main>
    )
  if (error)
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center p-space-5">
        <ErrorState
          title="Couldn't load plugins"
          message={error.message}
          action={null}
        />
      </main>
    )
  if (!plugin)
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center p-space-5">
        <EmptyState
          icon={PuzzlePieceIcon}
          title="Plugin not installed"
          description={`No plugin named "${name}" is installed for you.`}
          action={
            <Button
              size="sm"
              variant="outlined"
              as={<Link to="/my-settings/plugins" />}
            >
              Manage plugins
            </Button>
          }
        />
      </main>
    )
  return (
    <InstalledPluginPage
      key={plugin.id}
      plugin={plugin}
      path={path}
      active={active}
      onPath={onPath}
    />
  )
}

function InstalledPluginPage({
  plugin,
  path,
  active,
  onPath,
}: {
  plugin: InstalledPlugin
  path: string
  active: boolean
  onPath: (path: string) => void
}) {
  const navigate = useNavigate()
  const onScreen = useEffectEvent(() => active)
  const [title, setTitle] = useState<string | null>(null)
  useEffect(() => {
    if (!active) return
    const documentTitle = pageTitle(title ?? plugin.manifest.displayName)
    document.title = documentTitle
    return () => {
      if (document.title === documentTitle) document.title = pageTitle("Plugin")
    }
  }, [active, title, plugin.manifest.displayName])
  const bundle = usePluginBundle(plugin)
  const approval = useApproval()
  const share = useSharePluginRepositories()
  const source = [plugin.source.repository, plugin.source.path]
    .filter(Boolean)
    .join("/")

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="max-md:pl-space-12 flex h-10 shrink-0 items-center gap-space-2 border-b border-default px-space-4 text-xs">
        <PluginIcon
          icon={plugin.manifest.sidebar.icon}
          size={14}
          className="shrink-0 text-icon-secondary"
        />
        <span className="truncate font-medium text-primary">
          {plugin.manifest.displayName}
        </span>
        <span className="truncate text-secondary" title={plugin.commit_sha}>
          {source}@{plugin.commit_sha.slice(0, 7)}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-space-1 text-secondary">
          <ShieldCheckIcon size={14} />
          {plugin.scope === "organization"
            ? "Installed for everyone"
            : "Installed by you"}
        </span>
        <Link
          to="/my-settings/plugins"
          className="shrink-0 text-secondary underline-offset-2 hover:text-primary hover:underline"
        >
          Manage
        </Link>
      </header>
      {approval.shown && (
        <ApprovalPanel
          plugin={plugin}
          approval={approval.shown}
          armed={approval.armed}
          onAnswer={approval.answer}
        />
      )}
      {bundle.data !== undefined ? (
        <PluginFrame
          plugin={plugin}
          html={bundle.data}
          path={path}
          navigate={(next, replace) => {
            onPath(next)
            if (onScreen())
              void navigate({
                to: "/agents/plugins/$name/$",
                params: { name: plugin.name, _splat: next.slice(1) },
                replace,
              })
          }}
          openThread={(threadId) => {
            if (onScreen())
              void navigate({ to: "/agents/$threadId", params: { threadId } })
          }}
          setTitle={setTitle}
          visible={onScreen}
          confirmLink={async (url) =>
            onScreen() && (await approval.ask({ kind: "link", url })) !== null
          }
          confirmThread={async (request) =>
            onScreen() &&
            (await approval.ask({ kind: "thread", request })) !== null
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
        />
      ) : bundle.error ? (
        <div className="flex flex-1 items-center justify-center p-space-5">
          <ErrorState
            title={`Couldn't load ${plugin.manifest.displayName}`}
            message={bundle.error.message}
            action={null}
          />
        </div>
      ) : (
        <Skeleton className="m-space-5 flex-1" />
      )}
    </main>
  )
}
