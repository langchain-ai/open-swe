import type { PluginPermission } from "@openswe/plugin-sdk"
import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import { Input } from "@langchain/macaw-components/Input"
import { Switch } from "@langchain/macaw-components/Switch"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { PermissionList } from "@/features/plugins/components/PluginsSettings"
import type { App } from "@/features/plugins/lib/api"
import { useApps, useAppVersion } from "@/features/plugins/lib/queries"
import {
  INSTANCE_SCOPE,
  useScopedSettings,
  type ScopedSettings,
} from "@/features/settings/lib/settingsScope"
import type { FeaturedApp } from "@/lib/api"

/** Instance admins add listed apps to everyone's web UI. */
export function FeaturedAppsSection() {
  const settings = useScopedSettings(INSTANCE_SCOPE)
  return (
    <FeaturedAppsSettings
      settings={settings}
      editable={settings.data !== undefined}
    />
  )
}

/**
 * Listed apps every person's web UI has, their permissions approved for them,
 * each at the release an admin turned on and reading the repositories the
 * admin names; an owner's later releases reach no one until an admin updates it.
 */
export function FeaturedAppsSettings({
  settings,
  editable,
}: {
  settings: ScopedSettings
  editable: boolean
}) {
  const apps = useApps()
  const chosen = settings.data?.featured_apps ?? []
  const pinned = new Map(chosen.map((entry) => [entry.app, entry]))
  const offered = (apps.data ?? []).filter(
    (app) => app.listed || pinned.has(app.id)
  )
  // Pinned apps an admin can't see, once their owner stopped listing them.
  const missing = chosen.filter(
    (entry) => !offered.some((app) => app.id === entry.app)
  )

  const save = (next: Array<FeaturedApp>) =>
    settings.save({ featured_apps: next })
  const without = (id: string) => chosen.filter((entry) => entry.app !== id)
  const replace = (entry: FeaturedApp) =>
    save(chosen.map((found) => (found.app === entry.app ? entry : found)))
  const pin = (app: App) => {
    if (!app.latest) return
    const current = pinned.get(app.id)
    const entry: FeaturedApp = {
      app: app.id,
      release: app.latest.id,
      repositories: current?.repositories ?? [],
    }
    if (current) replace(entry)
    else save([...chosen, entry])
  }

  return (
    <SettingsSection
      title="Apps for everyone"
      description="Add a listed app to everyone's sidebar, its permissions approved for them. People can't remove it while it's on, and it stays on the release you turned on until you update it. Turning it off takes it back from everyone who never changed it."
    >
      <div className="divide-y divide-default">
        {offered.map((app) => {
          const entry = pinned.get(app.id)
          return (
            <FeaturedAppRow
              key={app.id}
              app={app}
              entry={entry}
              editable={editable}
              onPin={() => pin(app)}
              onOff={() => save(without(app.id))}
              onRepositories={(repositories) =>
                entry && replace({ ...entry, repositories })
              }
            />
          )
        })}
        {missing.map((entry) => (
          <SettingsRow
            key={entry.app}
            label="An app you can't see"
            description="Its owner stopped listing it, so it's added for no one; turn it off to clear it."
            control={
              <Switch
                aria-label={`Add app ${entry.app} for everyone`}
                checked
                onChange={() => save(without(entry.app))}
                disabled={!editable}
              />
            }
          />
        ))}
        {!apps.isPending && offered.length === 0 && missing.length === 0 && (
          <SettingsRow
            label="No listed apps yet"
            description="An app appears here once its owner lists it for anyone to add."
            control={null}
          />
        )}
      </div>
    </SettingsSection>
  )
}

/**
 * One listed app, with the permissions everyone has it with. Turning it on,
 * or updating to a release that asks for more, waits for the admin to approve
 * those permissions for everyone.
 */
function FeaturedAppRow({
  app,
  entry,
  editable,
  onPin,
  onOff,
  onRepositories,
}: {
  app: App
  entry: FeaturedApp | undefined
  editable: boolean
  onPin: () => void
  onOff: () => void
  onRepositories: (repositories: Array<string>) => void
}) {
  const approved = useAppVersion(entry?.release)
  const [asking, setAsking] = useState<ReadonlyArray<PluginPermission> | null>(
    null
  )
  const latest = app.latest
  const behind =
    entry !== undefined && latest !== null && entry.release !== latest.id
  const granted = entry
    ? approved.data?.manifest.permissions
    : latest?.manifest.permissions
  const name = latest?.manifest.displayName ?? app.name

  // What turning it on, or updating, approves for everyone beyond what they have.
  const ask = () => {
    if (!latest) return
    const wanted = latest.manifest.permissions
    const had = entry ? approved.data?.manifest.permissions : []
    const more = had ? wanted.filter((p) => !had.includes(p)) : wanted
    if (entry && had && more.length === 0) onPin()
    else setAsking(more)
  }

  return (
    <div>
      <SettingsRow
        label={name}
        description={
          !app.listed
            ? "Its owner stopped listing it, so it's added for no one; turn it off to clear it."
            : behind
              ? `Everyone has the release that was newest when it was turned on. Release ${latest?.number} is out; update to give it to everyone.`
              : (latest?.manifest.description ?? "")
        }
        control={
          <div className="flex items-center gap-space-2">
            {behind && app.listed && (
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                onClick={ask}
                disabled={!editable}
              >
                Update
              </Button>
            )}
            <Switch
              aria-label={`Add ${app.name} for everyone`}
              checked={entry !== undefined}
              onChange={(on) => (on ? ask() : onOff())}
              disabled={!editable || (entry === undefined && !latest)}
            />
          </div>
        }
      />
      {granted && (
        <div className="px-space-4 pb-space-3">
          <PermissionList permissions={granted} />
        </div>
      )}
      {entry && app.listed && (
        <SharedRepositories
          // A new saved list starts a fresh draft.
          key={(entry.repositories ?? []).join(",")}
          name={app.name}
          entry={entry}
          editable={editable}
          onSave={onRepositories}
        />
      )}
      <Dialog
        open={asking !== null}
        onOpenChange={(open) => !open && setAsking(null)}
      >
        <DialogContent
          childrenClassName="gap-space-4 p-space-6"
          showClose={false}
        >
          <DialogTitle>
            {entry
              ? `Give everyone release ${latest?.number}?`
              : `Add ${name} for everyone?`}
          </DialogTitle>
          <DialogDescription>
            {entry
              ? "It asks for more than the release everyone has. You approve it for everyone, and nobody is asked."
              : "Everyone gets it in their sidebar with these permissions, approved for them, and can't remove it while it's on."}
          </DialogDescription>
          <PermissionList permissions={asking ?? []} />
          <div className="flex justify-end gap-space-2">
            <Button
              size="sm"
              variant="outlined"
              onClick={() => setAsking(null)}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => {
                setAsking(null)
                onPin()
              }}
            >
              {entry ? "Approve and update" : "Add for everyone"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/** The repositories a featured app reads for each person who can read them. */
function SharedRepositories({
  name,
  entry,
  editable,
  onSave,
}: {
  name: string
  entry: FeaturedApp
  editable: boolean
  onSave: (repositories: Array<string>) => void
}) {
  const saved = (entry.repositories ?? []).join(", ")
  const [draft, setDraft] = useState(saved)
  const repositories = draft.split(/[\s,]+/).filter(Boolean)
  const dirty = repositories.join(", ") !== saved
  return (
    <div className="flex flex-col gap-space-2 px-space-4 pb-space-4">
      <Input
        label="Repositories it reads for everyone"
        aria-label={`Repositories ${name} reads for everyone`}
        size="md"
        placeholder="owner/repo"
        hintText="Shared for each person who can read them on GitHub, so they needn't pick them. While the app is on, nobody can stop sharing them."
        value={draft}
        onChange={setDraft}
        disabled={!editable}
      />
      <div className="flex items-center gap-space-2">
        <Button
          size="xs"
          color="primary"
          disabled={!editable || !dirty}
          onClick={() => onSave(repositories)}
        >
          Save repositories
        </Button>
        {dirty && (
          <span className="text-xs text-secondary">Unsaved changes</span>
        )}
      </div>
    </div>
  )
}
