import { Button } from "@langchain/macaw-components/Button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { ErrorState } from "@langchain/macaw-components/ErrorState"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { PuzzlePieceIcon } from "@phosphor-icons/react/dist/ssr/PuzzlePiece"
import { ShieldCheckIcon } from "@phosphor-icons/react/dist/ssr/ShieldCheck"
import { Link, useNavigate } from "@tanstack/react-router"

import { PluginFrame } from "@/features/plugins/components/PluginFrame"
import type { InstalledPlugin } from "@/features/plugins/lib/api"
import { useApproval } from "@/features/plugins/lib/approval"
import { PluginIcon } from "@/features/plugins/lib/icons"
import { usePlugin, usePluginBundle } from "@/features/plugins/lib/queries"

export function PluginPage({ name, path }: { name: string; path: string }) {
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
  return <InstalledPluginPage key={plugin.id} plugin={plugin} path={path} />
}

function InstalledPluginPage({
  plugin,
  path,
}: {
  plugin: InstalledPlugin
  path: string
}) {
  const navigate = useNavigate()
  const bundle = usePluginBundle(plugin)
  const approval = useApproval()
  const thread =
    approval.shown?.kind === "thread" ? approval.shown.request : null
  const link = approval.shown?.kind === "link" ? approval.shown.url : null
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
      {bundle.data !== undefined ? (
        <PluginFrame
          plugin={plugin}
          html={bundle.data}
          path={path}
          navigate={(next, replace) =>
            void navigate({
              to: "/agents/plugins/$name/$",
              params: { name: plugin.name, _splat: next.slice(1) },
              replace,
            })
          }
          openThread={(threadId) =>
            void navigate({ to: "/agents/$threadId", params: { threadId } })
          }
          confirmLink={(url) => approval.ask({ kind: "link", url })}
          confirmThread={(request) => approval.ask({ kind: "thread", request })}
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
      <Dialog
        open={thread !== null}
        onOpenChange={(open) => !open && approval.answer(false)}
      >
        <DialogContent
          childrenClassName="gap-space-4 p-space-6"
          showClose={false}
        >
          <DialogTitle>Start an agent thread?</DialogTitle>
          <DialogDescription>
            {plugin.manifest.displayName} wants to start a private agent thread
            in{" "}
            <span className="font-medium text-primary">
              {thread?.repository}
            </span>
            . The agent works as you, with your access. Read the request before
            you approve it.
          </DialogDescription>
          <div className="flex flex-col gap-space-1">
            <span className="text-xs font-medium text-primary">
              {thread?.title}
            </span>
            <pre className="max-h-72 overflow-auto rounded-md border border-default bg-surface-level-1 p-space-3 text-xs whitespace-pre-wrap text-primary">
              {thread?.request}
            </pre>
          </div>
          <div className="flex justify-end gap-space-2">
            <Button
              size="sm"
              variant="outlined"
              onClick={() => approval.answer(false)}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!approval.armed}
              onClick={() => approval.answer(true)}
            >
              Start thread
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog
        open={link !== null}
        onOpenChange={(open) => !open && approval.answer(false)}
      >
        <DialogContent
          childrenClassName="gap-space-4 p-space-6"
          showClose={false}
        >
          <DialogTitle>Open this link?</DialogTitle>
          <DialogDescription>
            {plugin.manifest.displayName} wants to open a page outside Open SWE
            in a new tab.
          </DialogDescription>
          <code className="rounded-md border border-default bg-surface-level-1 p-space-2 text-xs break-all text-primary">
            {link}
          </code>
          <div className="flex justify-end gap-space-2">
            <Button
              size="sm"
              variant="outlined"
              onClick={() => approval.answer(false)}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!approval.armed}
              onClick={() => approval.answer(true)}
            >
              Open link
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </main>
  )
}
