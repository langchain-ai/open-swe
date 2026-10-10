import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Text } from "@langchain/macaw-components/Text"
import { AppWindowIcon } from "@phosphor-icons/react/dist/ssr/AppWindow"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { Link } from "@tanstack/react-router"

import type { SandboxApp } from "@/lib/api"
import {
  appKeys,
  useDeleteSandboxApp,
  useLaunchSandboxApp,
  useSandboxApps,
} from "@/features/apps/lib/queries"
import { usePendingVariables } from "@/lib/optimistic"

function formatUpdated(value: string | null): string {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

export function AppsPage() {
  const apps = useSandboxApps()
  const items = apps.data ?? []

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-space-5 py-space-6 max-md:pt-space-9">
        <Text as="h1" variant="h3" weight="medium" color="primary">
          Apps
        </Text>
        <p className="mt-space-1 text-xs text-secondary">
          Web apps Open SWE built and saved for you. Each runs in the sandbox it
          was built in and keeps its data there; opening one wakes the sandbox
          and restarts the app if it stopped.
        </p>

        <div className="mt-space-5">
          {apps.isLoading ? (
            <div className="space-y-space-2">
              <Skeleton className="h-16 w-full rounded-xl" />
              <Skeleton className="h-16 w-full rounded-xl" />
            </div>
          ) : items.length === 0 ? (
            <EmptyState
              className="rounded-xl border border-dashed border-default bg-surface-level-1"
              icon={AppWindowIcon}
              title="No apps yet"
              description="Ask Open SWE to build something and save it as an app, for example “build a team lunch poll and save it as an app”."
            />
          ) : (
            <div className="space-y-space-2">
              {items.map((app) => (
                <AppRow key={app.id} app={app} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function AppRow({ app }: { app: SandboxApp }) {
  const launch = useLaunchSandboxApp()
  const remove = useDeleteSandboxApp()
  const starting = usePendingVariables<string>(appKeys.launch).includes(app.id)

  const onOpen = () => {
    // Opened before the request so the browser treats it as a user-initiated popup.
    const tab = window.open("", "_blank")
    if (tab) tab.opener = null
    launch.mutate(app.id, {
      onSuccess: ({ url }) => {
        if (tab) tab.location.href = url
        else window.open(url, "_blank", "noopener")
      },
      onError: () => tab?.close(),
    })
  }

  const onDelete = () => {
    if (
      !window.confirm(
        `Remove ${app.name} from your apps? Its files stay in the sandbox.`
      )
    ) {
      return
    }
    remove.mutate(app.id)
  }

  return (
    <div className="flex items-center gap-space-3 rounded-xl border border-default bg-surface-level-1 px-space-4 py-space-3">
      <AppWindowIcon size={20} className="shrink-0 text-icon-secondary" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-primary">
          {app.name}
        </div>
        {app.description && (
          <div className="mt-0.5 truncate text-xs text-secondary">
            {app.description}
          </div>
        )}
        <div className="mt-space-1 flex flex-wrap gap-x-space-3 gap-y-space-1 text-xs text-tertiary">
          <span className="truncate font-mono">{app.workdir}</span>
          <span>Port {app.port}</span>
          {app.updated_at && <span>Saved {formatUpdated(app.updated_at)}</span>}
        </div>
      </div>
      <IconButton
        asChild
        icon={ChatCircleIcon}
        label="Open the thread that built this app"
        color="secondary"
        variant="plain"
        size="md"
        className="shrink-0"
      >
        <Link to="/agents/$threadId" params={{ threadId: app.thread_id }} />
      </IconButton>
      <IconButton
        icon={TrashIcon}
        label="Remove app"
        color="secondary"
        variant="plain"
        size="md"
        className="shrink-0"
        onClick={onDelete}
      />
      <Button
        color="secondary"
        variant="outlined"
        leftDecorator={ArrowSquareOutIcon}
        onClick={onOpen}
        disabled={starting}
        className="shrink-0"
      >
        {starting ? "Starting…" : "Open"}
      </Button>
    </div>
  )
}
