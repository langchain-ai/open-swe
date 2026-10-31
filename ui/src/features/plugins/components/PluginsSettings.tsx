import type { PluginPermission } from "@openswe/plugin-sdk"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Switch } from "@langchain/macaw-components/Switch"
import { ArrowsClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowsClockwise"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { UploadSimpleIcon } from "@phosphor-icons/react/dist/ssr/UploadSimple"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { Link } from "@tanstack/react-router"
import { useRef, useState } from "react"
import { toast } from "sonner"

import { SettingsSection } from "@/components/AppShell"
import type {
  App,
  AppVersion,
  InstalledPlugin,
} from "@/features/plugins/lib/api"
import { PluginIcon, pluginRoot } from "@/features/plugins/lib/icons"
import { describePermission } from "@/features/plugins/lib/permissions"
import {
  useApps,
  useInstallPlugin,
  usePlugins,
  usePublishApp,
  useSetAppListed,
  useUninstallPlugin,
  useUnsharePluginRepository,
  useUpgradePlugin,
} from "@/features/plugins/lib/queries"

const ROW = "flex flex-col gap-space-3 p-space-4"

export function PluginsSettings() {
  const plugins = usePlugins()
  const apps = useApps()
  const [adding, setAdding] = useState<AppVersion | null>(null)
  const installedApps = new Map(
    (plugins.data ?? []).map((plugin) => [plugin.plugin_id, plugin])
  )
  return (
    <div className="flex flex-col gap-space-8">
      <SettingsSection
        title="In your sidebar"
        description="Each app opens from the sidebar and runs in a sandbox that blocks its own requests. It acts as you only through the permissions listed here, in the repositories you shared with it."
      >
        {plugins.isPending ? (
          <div className={ROW}>
            <Skeleton className="h-12 w-full" />
          </div>
        ) : installedApps.size === 0 ? (
          <p className={`${ROW} text-sm text-secondary`}>
            No apps yet. Ask an agent to build one, or add one below.
          </p>
        ) : (
          plugins.data?.map((plugin) => (
            <InstalledAppRow
              key={plugin.id}
              plugin={plugin}
              latest={
                apps.data?.find((app) => app.id === plugin.plugin_id)?.latest ??
                null
              }
            />
          ))
        )}
      </SettingsSection>
      <SettingsSection
        title="Apps"
        description="Yours, and the ones others listed for anyone to add. An agent publishes one with publish_app, or upload a built app's .html file."
        action={<UploadApp onPublished={setAdding} />}
      >
        {apps.isPending ? (
          <div className={ROW}>
            <Skeleton className="h-12 w-full" />
          </div>
        ) : (apps.data ?? []).length === 0 ? (
          <p className={`${ROW} text-sm text-secondary`}>No apps yet.</p>
        ) : (
          apps.data?.map((app) => (
            <AppRow
              key={app.id}
              app={app}
              installed={installedApps.has(app.id)}
              onAdd={setAdding}
            />
          ))
        )}
      </SettingsSection>
      {adding && <AddDialog version={adding} onClose={() => setAdding(null)} />}
    </div>
  )
}

function origin(version: AppVersion): string {
  const { repository, ref, commit, thread } = version.origin
  if (repository)
    return [repository, ref, commit?.slice(0, 7)].filter(Boolean).join(" · ")
  return thread ? "Built in a thread" : "Uploaded"
}

function InstalledAppRow({
  plugin,
  latest,
}: {
  plugin: InstalledPlugin
  latest: AppVersion | null
}) {
  const unshare = useUnsharePluginRepository()
  const uninstall = useUninstallPlugin()
  const upgrade = useUpgradePlugin()
  const [reviewing, setReviewing] = useState(false)
  const newer = latest && latest.number > plugin.version.number ? latest : null
  const asksMore = newer
    ? newer.manifest.permissions.filter(
        (p) => !plugin.manifest.permissions.includes(p)
      )
    : []

  const update = (version: AppVersion) =>
    upgrade.mutate(
      {
        id: plugin.id,
        version: version.id,
        permissions: version.manifest.permissions,
      },
      {
        onSuccess: () => {
          setReviewing(false)
          toast.success(
            `Updated ${plugin.manifest.displayName} to #${version.number}`
          )
        },
        onError: (error) =>
          toast.error(`Couldn't update ${plugin.manifest.displayName}`, {
            description: error.message,
          }),
      }
    )

  return (
    <div className={ROW}>
      <div className="flex items-start gap-space-3">
        <PluginIcon
          icon={plugin.manifest.sidebar.icon}
          size={20}
          className="mt-0.5 shrink-0 text-icon-secondary"
        />
        <div className="flex min-w-0 flex-1 flex-col gap-space-1">
          <div className="flex flex-wrap items-center gap-space-2">
            <Link
              to="/agents/plugins/$name/$"
              params={{ name: plugin.name, _splat: "" }}
              className="text-sm font-medium text-primary hover:underline"
            >
              {plugin.manifest.displayName}
            </Link>
            <span className="text-xs text-secondary">
              v{plugin.manifest.version} · #{plugin.version.number}
            </span>
            {!plugin.mine && (
              <Badge size="xxs" color="secondary">
                Listed by someone else
              </Badge>
            )}
          </div>
          <span className="truncate text-xs text-secondary">
            {origin(plugin.version)}
          </span>
          {plugin.manifest.description && (
            <p className="text-xs text-secondary">
              {plugin.manifest.description}
            </p>
          )}
        </div>
        <div className="flex shrink-0 gap-space-1">
          {newer && (
            <Button
              size="xs"
              variant="outlined"
              leftDecorator={ArrowsClockwiseIcon}
              loading={upgrade.isPending}
              onClick={() =>
                asksMore.length ? setReviewing(true) : update(newer)
              }
            >
              Update to #{newer.number}
            </Button>
          )}
          <IconButton
            icon={TrashIcon}
            label={`Remove ${plugin.manifest.displayName}`}
            size="xs"
            onClick={() => uninstall.mutate(plugin.id)}
          />
        </div>
      </div>
      <PermissionList permissions={plugin.manifest.permissions} />
      <SharedRepositories
        repositories={plugin.repositories}
        onUnshare={(repository) =>
          unshare.mutate({ id: plugin.id, repository })
        }
      />
      <Dialog
        open={reviewing}
        onOpenChange={(open) => !open && setReviewing(false)}
      >
        <DialogContent
          childrenClassName="gap-space-4 p-space-6"
          showClose={false}
        >
          <DialogTitle>Approve new permissions?</DialogTitle>
          <DialogDescription>
            #{newer?.number} of {plugin.manifest.displayName} asks for more than
            you approved.
          </DialogDescription>
          <PermissionList permissions={asksMore} />
          <div className="flex justify-end gap-space-2">
            <Button
              size="sm"
              variant="outlined"
              onClick={() => setReviewing(false)}
            >
              Keep #{plugin.version.number}
            </Button>
            <Button
              size="sm"
              loading={upgrade.isPending}
              onClick={() => newer && update(newer)}
            >
              Approve and update
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function AppRow({
  app,
  installed,
  onAdd,
}: {
  app: App
  installed: boolean
  onAdd: (version: AppVersion) => void
}) {
  const setListed = useSetAppListed()
  const manifest = app.latest?.manifest
  return (
    <div className={`${ROW} sm:flex-row sm:items-start`}>
      <PluginIcon
        icon={manifest?.sidebar.icon ?? "puzzle-piece"}
        size={20}
        className="mt-0.5 shrink-0 text-icon-secondary"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-space-1">
        <span className="text-sm font-medium text-primary">
          {manifest?.displayName ?? app.name}{" "}
          {app.latest && (
            <span className="text-xs font-normal text-secondary">
              #{app.latest.number}
            </span>
          )}
        </span>
        {manifest?.description && (
          <p className="text-xs text-secondary">{manifest.description}</p>
        )}
        <span className="text-xs text-secondary">
          {app.latest ? origin(app.latest) : "Only previews so far"}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-space-3">
        {app.mine && (
          <label className="flex items-center gap-space-2 text-xs text-secondary">
            Anyone can add it
            <Switch
              aria-label={`Let anyone add ${manifest?.displayName ?? app.name}`}
              checked={app.listed}
              onChange={(listed) => setListed.mutate({ id: app.id, listed })}
            />
          </label>
        )}
        <Button
          size="sm"
          variant={installed ? "outlined" : "normal"}
          disabled={installed || !app.latest}
          onClick={() => app.latest && onAdd(app.latest)}
        >
          {installed ? "Added" : "Add"}
        </Button>
      </div>
    </div>
  )
}

function UploadApp({
  onPublished,
}: {
  onPublished: (version: AppVersion) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const publish = usePublishApp()
  const plugins = usePlugins()
  return (
    <>
      <input
        ref={input}
        type="file"
        accept=".html,text/html"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ""
          if (!file) return
          void file.text().then((html) =>
            publish.mutate(html, {
              onSuccess: (version) => {
                toast.success(
                  `Published ${version.manifest.displayName} #${version.number}`
                )
                if (
                  !plugins.data?.some(
                    (plugin) => plugin.name === version.manifest.name
                  )
                )
                  onPublished(version)
              },
            })
          )
        }}
      />
      <Button
        size="xs"
        variant="outlined"
        leftDecorator={UploadSimpleIcon}
        loading={publish.isPending}
        onClick={() => input.current?.click()}
      >
        Upload app
      </Button>
    </>
  )
}

function AddDialog({
  version,
  onClose,
}: {
  version: AppVersion
  onClose: () => void
}) {
  const install = useInstallPlugin()
  const { manifest } = version
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        childrenClassName="gap-space-4 p-space-6"
        showClose={false}
      >
        <DialogTitle>Add {manifest.displayName}?</DialogTitle>
        <DialogDescription>
          It runs in a sandbox that blocks its own requests, and acts as you
          only through what you allow here. It reaches no repository until you
          pick one when it asks.
        </DialogDescription>
        <div className="flex flex-col gap-space-2">
          <span className="text-xs font-medium text-primary">It can</span>
          <PermissionList permissions={manifest.permissions} />
        </div>
        <div className="flex justify-end gap-space-2">
          <Button size="sm" variant="outlined" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            loading={install.isPending}
            onClick={() =>
              install.mutate(
                { version: version.id, permissions: manifest.permissions },
                {
                  onSuccess: (installed) => {
                    toast.success(`Added ${installed.manifest.displayName}`, {
                      description: `Open it from the sidebar, at ${pluginRoot(installed.name)}.`,
                    })
                    onClose()
                  },
                }
              )
            }
          >
            Add to sidebar
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function PermissionList({
  permissions,
}: {
  permissions: ReadonlyArray<PluginPermission>
}) {
  if (permissions.length === 0)
    return <p className="text-xs text-secondary">Asks for no permissions.</p>
  return (
    <ul className="flex flex-col gap-space-1">
      {permissions.map((permission) => {
        const copy = describePermission(permission)
        return (
          <li key={permission} className="text-xs text-secondary">
            <span className="font-medium text-primary">{copy.label}</span>
            {copy.description && ` — ${copy.description}`}
          </li>
        )
      })}
    </ul>
  )
}

/** What the viewer shared with the app when it asked. */
function SharedRepositories({
  repositories,
  onUnshare,
}: {
  repositories: ReadonlyArray<string>
  onUnshare: (repository: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-space-1">
      <span className="mr-space-1 text-xs text-secondary">
        Repositories you shared:
      </span>
      {repositories.length === 0 && (
        <span className="text-xs text-secondary">
          none yet; it asks when it needs one
        </span>
      )}
      {repositories.map((repository) => (
        <Badge key={repository} size="xs" color="plain">
          <span className="flex items-center gap-space-1">
            {repository}
            <button
              type="button"
              aria-label={`Stop sharing ${repository}`}
              className="text-icon-secondary hover:text-primary"
              onClick={() => onUnshare(repository)}
            >
              <XIcon size={10} />
            </button>
          </span>
        </Badge>
      ))}
    </div>
  )
}
