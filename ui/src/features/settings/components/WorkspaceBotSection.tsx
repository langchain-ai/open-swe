import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Select } from "@langchain/macaw-components/Select"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { useScopedSettings } from "@/features/settings/lib/settingsScope"
import { api, ApiError, type ManagedToolsProfile } from "@/lib/api"
import { SignInWithLangSmithButton } from "./ConnectionsSection"
import {
  ConnectButton,
  managedToolsQuery,
  useConnectPopup,
} from "./ManagedToolsSection"

const NONE = "none"
const KITS_KEY = ["managedToolsKits"]
const PROFILES_KEY = ["managedToolsProfiles"]
const botToolsKey = (slug: string) => ["workspaceBotTools", slug]

/** Options for a picker of LMT ids, keeping a saved id selectable when the list can't be read. */
function pickerOptions(
  items: Array<{ value: string; label: string }>,
  current: string | null,
  unlisted: string
) {
  return [
    { value: NONE, label: "None" },
    ...items,
    ...(current && !items.some((item) => item.value === current)
      ? [{ value: current, label: unlisted }]
      : []),
  ]
}

/**
 * The workspace bot: a LangSmith Managed Tools kit every run in the workspace
 * loads, called as an LMT profile that holds the bot's own connections.
 */
export function WorkspaceBotSection({
  slug,
  workspaceName,
}: {
  slug: string
  workspaceName: string
}) {
  const qc = useQueryClient()
  const settings = useScopedSettings({ kind: "workspace", slug }, () => {
    void qc.invalidateQueries({ queryKey: botToolsKey(slug) })
  })
  const managed = useQuery(managedToolsQuery)
  const kits = useQuery({
    queryKey: KITS_KEY,
    queryFn: api.listManagedToolsKits,
    retry: false,
  })
  const profiles = useQuery({
    queryKey: PROFILES_KEY,
    queryFn: api.listManagedToolsProfiles,
    retry: false,
  })
  const bot = useQuery({
    queryKey: botToolsKey(slug),
    queryFn: () => api.getWorkspaceBotTools(slug),
  })
  const popup = useConnectPopup(botToolsKey(slug), (serverId) =>
    api.connectWorkspaceBotTool(slug, serverId)
  )
  const profileName = `Open SWE: ${workspaceName}`
  // LangSmith picks the new profile's id, so it can't be shown before it exists.
  const createProfile = useMutation({
    meta: { errorTitle: "Couldn't create the profile" },
    mutationFn: () => api.createManagedToolsProfile(profileName),
    onSuccess: (profile) => {
      qc.setQueryData<Array<ManagedToolsProfile>>(PROFILES_KEY, (current) => [
        ...(current ?? []),
        profile,
      ])
      settings.save({ managed_tools_profile_id: profile.id })
    },
  })

  if (managed.data && !managed.data.configured) return null

  const kitId = settings.data?.managed_tools_gateway_id ?? null
  const profileId = settings.data?.managed_tools_profile_id ?? null
  const signInNeeded = [kits.error, profiles.error].some(
    (error) => error instanceof ApiError && error.status === 409
  )
  const listError = kits.error ?? profiles.error
  const status = bot.data?.status
  const services = status?.servers ?? []

  return (
    <SettingsSection
      title="Workspace bot"
      description="Tools every run in this workspace can use: a LangSmith Managed Tools kit, called as a LangSmith profile that holds the bot's own connections. In someone's private threads, a service they connected themselves replaces the bot's service of the same name."
    >
      {signInNeeded ? (
        <SettingsRow
          label="LangSmith"
          description="Your LangSmith sign-in expired. Sign in again to list tool kits and profiles."
          control={<SignInWithLangSmithButton />}
        />
      ) : listError ? (
        <p role="alert" className="p-space-4 text-xs text-error-secondary">
          {`Tool kits or profiles could not be listed: ${listError.message}`}
        </p>
      ) : null}
      <SettingsRow
        label="Tool kit"
        description="The tools the bot offers."
        control={
          <Select
            aria-label="Tool kit"
            options={pickerOptions(
              (kits.data ?? []).map((kit) => ({
                value: kit.id,
                label: `${kit.name} · ${kit.tool_count} ${kit.tool_count === 1 ? "tool" : "tools"}`,
              })),
              kitId,
              "Configured tool kit"
            )}
            value={kitId ?? NONE}
            onChange={(next) =>
              next &&
              settings.save({
                managed_tools_gateway_id: next === NONE ? null : next,
              })
            }
            disabled={!settings.data || kits.isLoading}
            triggerClassName="w-64"
          />
        }
      />
      <SettingsRow
        label="Bot profile"
        description={`The credentials the bot calls them with. Create profile makes one named “${profileName}”.`}
        control={
          <div className="flex items-center gap-space-2">
            <Select
              aria-label="Bot profile"
              options={pickerOptions(
                (profiles.data ?? []).map((profile) => ({
                  value: profile.id,
                  label: profile.name,
                })),
                profileId,
                "Configured profile"
              )}
              value={profileId ?? NONE}
              onChange={(next) =>
                next &&
                settings.save({
                  managed_tools_profile_id: next === NONE ? null : next,
                })
              }
              disabled={!settings.data || profiles.isLoading}
              triggerClassName="w-64"
            />
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              disabled={!settings.data || createProfile.isPending}
              onClick={() => createProfile.mutate()}
            >
              {createProfile.isPending ? "Creating…" : "Create profile"}
            </Button>
          </div>
        }
      />
      {kitId && profileId ? (
        bot.data?.error ? (
          <p role="alert" className="p-space-4 text-xs text-error-secondary">
            {`The bot's tools could not be read: ${bot.data.error}`}
          </p>
        ) : bot.isLoading ? (
          <p className="p-space-4 text-xs text-secondary">Loading…</p>
        ) : bot.isError ? (
          <p role="alert" className="p-space-4 text-xs text-error-secondary">
            {bot.error.message}
          </p>
        ) : !status ? (
          <p className="p-space-4 text-xs text-secondary">
            Open SWE has no LangSmith API key, so the workspace bot can't load
            its tools.
          </p>
        ) : (
          <>
            {services.map((server) => (
              <SettingsRow
                key={server.id}
                label={server.name}
                description={`${server.tool_count} ${server.tool_count === 1 ? "tool" : "tools"}`}
                control={
                  server.connected ? (
                    <Badge color="primary" size="xs">
                      Connected
                    </Badge>
                  ) : (
                    <ConnectButton
                      server={server}
                      label="Connect for bot"
                      connecting={popup.connecting === server.id}
                      onConnect={popup.start}
                    />
                  )
                }
              />
            ))}
            <p className="px-space-4 py-space-3 text-xs text-secondary">
              {services.length === 0
                ? "This tool kit has no services yet. "
                : "Connecting a service signs it in for the bot, not for you. "}
              Edit the kit&apos;s tools in LangSmith under Settings &gt; Tools.
            </p>
          </>
        )
      ) : null}
    </SettingsSection>
  )
}
