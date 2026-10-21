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
import { Input } from "@langchain/macaw-components/Input"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ArrowsClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowsClockwise"
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { Link } from "@tanstack/react-router"
import { useState } from "react"
import { toast } from "sonner"

import { SettingsSection } from "@/components/AppShell"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import type {
  CatalogItem,
  InstalledPlugin,
  PluginSource,
} from "@/features/plugins/lib/api"
import { unapprovedPermissions } from "@/features/plugins/lib/api"
import { PluginIcon, pluginRoot } from "@/features/plugins/lib/icons"
import { describePermission } from "@/features/plugins/lib/permissions"
import {
  useInstallPlugin,
  usePluginCatalog,
  usePlugins,
  useSetPluginRepositories,
  useUninstallPlugin,
  useUpgradePlugin,
} from "@/features/plugins/lib/queries"
import { useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"

const ROW = "flex flex-col gap-space-3 p-space-4"

export function PluginsSettings() {
  return (
    <div className="flex flex-col gap-space-8">
      <InstalledPlugins />
      <InstallFromGitHub />
    </div>
  )
}

function canManage(plugin: InstalledPlugin, admin: boolean): boolean {
  return plugin.scope === "user" || admin
}

function InstalledPlugins() {
  const plugins = usePlugins()
  const admin = useSession().data?.is_admin === true
  return (
    <SettingsSection
      title="Installed"
      description="Each plugin opens from the sidebar and runs in a sandbox with no network. It acts as you, only through the permissions and repositories listed here."
    >
      {plugins.isPending ? (
        <div className={ROW}>
          <Skeleton className="h-12 w-full" />
        </div>
      ) : (plugins.data ?? []).length === 0 ? (
        <p className={`${ROW} text-sm text-secondary`}>
          No plugins yet. Install one from a GitHub repository below.
        </p>
      ) : (
        plugins.data?.map((plugin) => (
          <InstalledPluginRow
            key={plugin.id}
            plugin={plugin}
            editable={canManage(plugin, admin)}
          />
        ))
      )}
    </SettingsSection>
  )
}

function InstalledPluginRow({
  plugin,
  editable,
}: {
  plugin: InstalledPlugin
  editable: boolean
}) {
  const setRepositories = useSetPluginRepositories()
  const uninstall = useUninstallPlugin()
  const upgrade = useUpgradePlugin()
  const [newPermissions, setNewPermissions] =
    useState<Array<PluginPermission> | null>(null)

  const update = (permissions: Array<PluginPermission>) =>
    upgrade.mutate(
      { id: plugin.id, permissions },
      {
        onSuccess: (updated) => {
          setNewPermissions(null)
          toast.success(
            updated.commit_sha === plugin.commit_sha
              ? `${plugin.manifest.displayName} is up to date`
              : `Updated ${plugin.manifest.displayName} to ${updated.commit_sha.slice(0, 7)}`
          )
        },
        onError: (error) => {
          const unapproved = unapprovedPermissions(error)
          if (unapproved) setNewPermissions(unapproved)
          else
            toast.error(`Couldn't update ${plugin.manifest.displayName}`, {
              description: error.message,
            })
        },
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
              v{plugin.manifest.version}
            </span>
            <Badge size="xxs" color="secondary">
              {plugin.scope === "organization" ? "Everyone" : "Just you"}
            </Badge>
          </div>
          <span className="truncate text-xs text-secondary">
            {[plugin.source.repository, plugin.source.path]
              .filter(Boolean)
              .join("/")}
            {plugin.source.ref ? ` · ${plugin.source.ref}` : ""} @
            {plugin.commit_sha.slice(0, 7)}
          </span>
          {plugin.manifest.description && (
            <p className="text-xs text-secondary">
              {plugin.manifest.description}
            </p>
          )}
        </div>
        {editable && (
          <div className="flex shrink-0 gap-space-1">
            <Button
              size="xs"
              variant="outlined"
              leftDecorator={ArrowsClockwiseIcon}
              loading={upgrade.isPending}
              onClick={() => update(plugin.manifest.permissions)}
            >
              Update
            </Button>
            <IconButton
              icon={TrashIcon}
              label={`Uninstall ${plugin.manifest.displayName}`}
              size="xs"
              onClick={() => uninstall.mutate(plugin.id)}
            />
          </div>
        )}
      </div>
      <PermissionList permissions={plugin.manifest.permissions} />
      <RepositoryGrants
        repositories={plugin.repositories}
        editable={editable}
        onChange={(repositories) =>
          setRepositories.mutate({ id: plugin.id, repositories })
        }
      />
      <Dialog
        open={newPermissions !== null}
        onOpenChange={(open) => !open && setNewPermissions(null)}
      >
        <DialogContent
          childrenClassName="gap-space-4 p-space-6"
          showClose={false}
        >
          <DialogTitle>Approve new permissions?</DialogTitle>
          <DialogDescription>
            The latest {plugin.manifest.displayName} asks for more than you
            approved.
          </DialogDescription>
          <PermissionList permissions={newPermissions ?? []} />
          <div className="flex justify-end gap-space-2">
            <Button
              size="sm"
              variant="outlined"
              onClick={() => setNewPermissions(null)}
            >
              Keep current version
            </Button>
            <Button
              size="sm"
              loading={upgrade.isPending}
              onClick={() =>
                update([
                  ...plugin.manifest.permissions,
                  ...(newPermissions ?? []),
                ])
              }
            >
              Approve and update
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function PermissionList({
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

function RepositoryGrants({
  repositories,
  editable,
  onChange,
}: {
  repositories: ReadonlyArray<string>
  editable: boolean
  onChange: (repositories: Array<string>) => void
}) {
  const repos = useRepos()
  return (
    <div className="flex flex-wrap items-center gap-space-1">
      <span className="mr-space-1 text-xs text-secondary">Repositories:</span>
      {repositories.length === 0 && (
        <span className="text-xs text-secondary">none</span>
      )}
      {repositories.map((repository) => (
        <Badge key={repository} size="xs" color="plain">
          <span className="flex items-center gap-space-1">
            {repository}
            {editable && (
              <button
                type="button"
                aria-label={`Remove ${repository}`}
                className="text-icon-secondary hover:text-primary"
                onClick={() =>
                  onChange(repositories.filter((name) => name !== repository))
                }
              >
                <XIcon size={10} />
              </button>
            )}
          </span>
        </Badge>
      ))}
      {editable && (
        <RepoSelector
          repos={repos.data?.repositories?.filter(
            (repo) => !repositories.includes(repo.full_name.toLowerCase())
          )}
          onRepoChange={(repo) =>
            repo && onChange([...repositories, repo.toLowerCase()])
          }
          placeholder="Add repository"
          triggerClassName="h-6 text-xs"
        />
      )}
    </div>
  )
}

function InstallFromGitHub() {
  const [repository, setRepository] = useState("")
  const [path, setPath] = useState("")
  const [ref, setRef] = useState("")
  const [source, setSource] = useState<PluginSource | null>(null)
  const catalog = usePluginCatalog(source)
  const [reviewing, setReviewing] = useState<CatalogItem | null>(null)

  return (
    <SettingsSection
      title="Install from GitHub"
      description="A plugin is a directory with .openswe-plugin/plugin.json; a marketplace repository lists several in .openswe-plugin/marketplace.json. Try langchain-ai/open-swe."
    >
      <form
        className={`${ROW} sm:flex-row sm:items-end`}
        onSubmit={(event) => {
          event.preventDefault()
          if (repository.trim())
            setSource({
              repository: repository.trim(),
              path: path.trim(),
              ref: ref.trim() || null,
            })
        }}
      >
        <Input
          className="flex-1"
          label="Repository"
          placeholder="owner/repo"
          value={repository}
          onChange={setRepository}
          debounceMs={0}
        />
        <Input
          className="sm:w-48"
          label="Directory"
          placeholder="optional"
          value={path}
          onChange={setPath}
          debounceMs={0}
        />
        <Input
          className="sm:w-36"
          label="Branch or tag"
          placeholder="default branch"
          value={ref}
          onChange={setRef}
          debounceMs={0}
        />
        <Button
          type="submit"
          size="md"
          leftDecorator={MagnifyingGlassIcon}
          loading={catalog.isFetching}
          disabled={!repository.trim()}
        >
          Find plugins
        </Button>
      </form>
      {catalog.error && (
        <p className={`${ROW} text-error text-sm`}>{catalog.error.message}</p>
      )}
      {catalog.data?.length === 0 && (
        <p className={`${ROW} text-sm text-secondary`}>
          That marketplace lists no plugins this account can read.
        </p>
      )}
      {catalog.data?.map((item) => (
        <CatalogRow
          key={`${item.source.repository}/${item.source.path}`}
          item={item}
          onInstall={() => setReviewing(item)}
        />
      ))}
      {reviewing && (
        <InstallDialog item={reviewing} onClose={() => setReviewing(null)} />
      )}
    </SettingsSection>
  )
}

function CatalogRow({
  item,
  onInstall,
}: {
  item: CatalogItem
  onInstall: () => void
}) {
  const plugins = usePlugins()
  const installed = plugins.data?.some(
    (plugin) => plugin.name === item.manifest.name
  )
  return (
    <div className={`${ROW} sm:flex-row sm:items-start`}>
      <PluginIcon
        icon={item.manifest.sidebar.icon}
        size={20}
        className="mt-0.5 shrink-0 text-icon-secondary"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-space-1">
        <span className="text-sm font-medium text-primary">
          {item.manifest.displayName}{" "}
          <span className="text-xs font-normal text-secondary">
            v{item.manifest.version}
          </span>
        </span>
        {item.manifest.description && (
          <p className="text-xs text-secondary">{item.manifest.description}</p>
        )}
        <span className="text-xs text-secondary">
          {[item.source.repository, item.source.path].filter(Boolean).join("/")}
        </span>
      </div>
      <Button
        size="sm"
        variant={installed ? "outlined" : "normal"}
        disabled={installed}
        onClick={onInstall}
      >
        {installed ? "Installed" : "Install"}
      </Button>
    </div>
  )
}

function InstallDialog({
  item,
  onClose,
}: {
  item: CatalogItem
  onClose: () => void
}) {
  const admin = useSession().data?.is_admin === true
  const install = useInstallPlugin()
  const [scope, setScope] = useState<"user" | "organization">("user")
  const [repositories, setRepositories] = useState<Array<string>>([])
  const needsRepositories = item.manifest.permissions.some(
    (permission) => permission !== "storage"
  )

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        childrenClassName="gap-space-4 p-space-6"
        showClose={false}
      >
        <DialogTitle>Install {item.manifest.displayName}?</DialogTitle>
        <DialogDescription>
          From{" "}
          {[item.source.repository, item.source.path].filter(Boolean).join("/")}
          . It runs in a sandbox with no network, and acts as you only through
          what you allow here.
        </DialogDescription>
        <div className="flex flex-col gap-space-2">
          <span className="text-xs font-medium text-primary">It can</span>
          <PermissionList permissions={item.manifest.permissions} />
        </div>
        <div className="flex flex-col gap-space-2">
          <span className="text-xs font-medium text-primary">
            In these repositories
          </span>
          <RepositoryGrants
            repositories={repositories}
            editable
            onChange={setRepositories}
          />
        </div>
        {admin && (
          <label className="flex items-center gap-space-2 text-xs text-primary">
            <input
              type="checkbox"
              checked={scope === "organization"}
              onChange={(event) =>
                setScope(event.target.checked ? "organization" : "user")
              }
            />
            Install for everyone in this Open SWE
          </label>
        )}
        <div className="flex justify-end gap-space-2">
          <Button size="sm" variant="outlined" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            loading={install.isPending}
            disabled={needsRepositories && repositories.length === 0}
            onClick={() =>
              install.mutate(
                {
                  repository: item.source.repository,
                  path: item.source.path,
                  ref: item.source.ref,
                  scope,
                  name: item.manifest.name,
                  permissions: item.manifest.permissions,
                  repositories,
                },
                {
                  onSuccess: (installed) => {
                    toast.success(
                      `Installed ${installed.manifest.displayName}`,
                      {
                        description: `Open it from the sidebar, at ${pluginRoot(installed.name)}.`,
                      }
                    )
                    onClose()
                  },
                }
              )
            }
          >
            Install
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
