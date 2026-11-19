import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { Link as MacawLink } from "@langchain/macaw-components/Link"
import { Select, type SelectOption } from "@langchain/macaw-components/Select"
import { Switch } from "@langchain/macaw-components/Switch"

import { SettingsPanel, SettingsSection } from "@/components/AppShell"
import {
  api,
  type ChannelMode,
  type RoutingSettings as RoutingData,
} from "@/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"
import {
  slackChannelHref,
  slackChannelLabel,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import { cn, INHERITED_LINK } from "@/lib/utils"
import { RepositoryPicker, SlackChannelPicker } from "./RoutingBindingPickers"

type RoutingChange =
  | { kind: "repository"; repo: string; allowed: boolean | null }
  | { kind: "channel"; id: string; mode: ChannelMode | null }

const QUERY_KEY = ["routing"]
const CHANNEL_MODES: Array<SelectOption<ChannelMode>> = [
  { value: "kitchen", label: "Kitchen" },
  { value: "ask", label: "Ask" },
]

export function RoutingSettings() {
  const qc = useQueryClient()
  const routing = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.getRouting,
    meta: { invalidatedBy: [invalidationTopic("routing")] },
  })
  const directory = useSlackChannelDirectory(true)
  const save = useMutation({
    mutationKey: ["routing", "save"],
    scope: { id: "routing" },
    meta: { errorTitle: "Couldn't save routing settings" },
    mutationFn: async (changes: RoutingChange[]) => {
      for (const change of changes) {
        if (change.kind === "repository") {
          if (change.allowed === null)
            await api.removeRoutingRepository(change.repo)
          else await api.configureRoutingRepository(change.repo, change.allowed)
        } else if (change.mode === null)
          await api.removeRoutingChannel(change.id)
        else await api.configureRoutingChannel(change.id, change.mode)
      }
    },
    onMutate: async (changes) => {
      await qc.cancelQueries({ queryKey: QUERY_KEY })
      const previous = qc.getQueryData<RoutingData>(QUERY_KEY)
      qc.setQueryData<RoutingData>(QUERY_KEY, (current) => {
        if (!current) return current
        let repos = current.repos
        let channels = current.slack_channels
        for (const change of changes) {
          if (change.kind === "repository") {
            repos = repos.filter(
              (row) => row.repo.toLowerCase() !== change.repo.toLowerCase()
            )
            if (change.allowed !== null)
              repos = [
                ...repos,
                { repo: change.repo, may_start_threads: change.allowed },
              ]
          } else {
            channels = channels.filter((row) => row.channel_id !== change.id)
            if (change.mode !== null)
              channels = [
                ...channels,
                { channel_id: change.id, mode: change.mode },
              ]
          }
        }
        return { repos, slack_channels: channels }
      })
      return { previous }
    },
    onError: (_error, _changes, context) => {
      if (context?.previous) qc.setQueryData(QUERY_KEY, context.previous)
    },
    onSettled: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })
  const repositories = [...(routing.data?.repos ?? [])].sort((a, b) =>
    a.repo.localeCompare(b.repo)
  )
  const selectedRepos = repositories.map((row) => row.repo)
  const channels = routing.data?.slack_channels ?? []
  const channelIds = channels.map((row) => row.channel_id)
  const disabled = save.isPending || !routing.data

  if (!routing.data)
    return (
      <SettingsSection title="Routing">
        <SettingsPanel>
          {routing.isError ? (
            <>
              <p role="alert" className="text-sm text-error-secondary">
                Couldn't load routing settings.
              </p>
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                onClick={() => void routing.refetch()}
              >
                Retry
              </Button>
            </>
          ) : (
            <p role="status" className="text-sm text-secondary">
              Loading routing settings…
            </p>
          )}
        </SettingsPanel>
      </SettingsSection>
    )

  return (
    <>
      <SettingsSection
        title="Repositories"
        description="Bind repositories for inbound GitHub and Linear routing. Binding is not an access grant or a preload setting. Can start threads lets a GitHub Actions workflow create ownerless threads with its own OIDC-issued token."
      >
        <SettingsPanel>
          {save.error && (
            <p role="alert" className="text-sm text-error-secondary">
              {save.error.message}
            </p>
          )}
          {repositories.length === 0 ? (
            <p className="text-sm text-secondary">No bound repositories.</p>
          ) : (
            <ul className="divide-y divide-default">
              {repositories.map((row) => (
                <li
                  key={row.repo.toLowerCase()}
                  className="flex flex-wrap items-center justify-between gap-space-3 py-space-2"
                >
                  <span className="min-w-0 font-mono text-xs break-all text-primary">
                    {row.repo}
                  </span>
                  <label className="flex items-center gap-space-2 text-xs text-secondary">
                    Can start threads
                    <Switch
                      aria-label={`Let ${row.repo} start threads`}
                      checked={row.may_start_threads}
                      disabled={disabled}
                      onChange={(allowed) =>
                        save.mutate([
                          { kind: "repository", repo: row.repo, allowed },
                        ])
                      }
                    />
                  </label>
                </li>
              ))}
            </ul>
          )}
          <RepositoryPicker
            selected={selectedRepos}
            disabled={disabled}
            onChange={(next) => {
              const desired = new Set(next.map((repo) => repo.toLowerCase()))
              const known = new Set(
                selectedRepos.map((repo) => repo.toLowerCase())
              )
              const changes: RoutingChange[] = [
                ...selectedRepos
                  .filter((repo) => !desired.has(repo.toLowerCase()))
                  .map((repo) => ({
                    kind: "repository" as const,
                    repo,
                    allowed: null,
                  })),
                ...next
                  .filter((repo) => !known.has(repo.toLowerCase()))
                  .map((repo) => ({
                    kind: "repository" as const,
                    repo,
                    allowed: false,
                  })),
              ]
              if (changes.length) save.mutate(changes)
            }}
          />
        </SettingsPanel>
      </SettingsSection>
      <SettingsSection
        title="Slack channels"
        description="Enable Kitchen or Ask in internal channels Robin has joined. Both let Robin respond without an @mention. Ask investigates reports and suggests next steps."
      >
        <SettingsPanel>
          {channels.length === 0 ? (
            <p className="text-sm text-secondary">
              No channels configured for Kitchen or Ask.
            </p>
          ) : (
            <ul className="divide-y divide-default">
              {channels.map((row) => {
                const label = slackChannelLabel(
                  directory.data,
                  row.channel_id,
                  channelIds
                )
                return (
                  <li
                    key={row.channel_id}
                    className="flex flex-wrap items-center justify-between gap-space-3 py-space-2"
                  >
                    <MacawLink
                      variant="xs"
                      href={slackChannelHref(row.channel_id)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={cn(
                        INHERITED_LINK,
                        "min-w-0 truncate text-primary [&>span]:truncate"
                      )}
                    >
                      {label}
                    </MacawLink>
                    <Select
                      aria-label={`Channel type for ${label}`}
                      size="sm"
                      triggerClassName="w-32 shrink-0"
                      value={row.mode}
                      options={CHANNEL_MODES}
                      disabled={disabled}
                      onChange={(mode) => {
                        if (mode)
                          save.mutate([
                            { kind: "channel", id: row.channel_id, mode },
                          ])
                      }}
                    />
                  </li>
                )
              })}
            </ul>
          )}
          <SlackChannelPicker
            selected={channelIds}
            disabled={disabled}
            onChange={(next) => {
              const desired = new Set(next)
              const known = new Set(channelIds)
              const changes: RoutingChange[] = [
                ...channelIds
                  .filter((id) => !desired.has(id))
                  .map((id) => ({ kind: "channel" as const, id, mode: null })),
                ...next
                  .filter((id) => !known.has(id))
                  .map((id) => ({
                    kind: "channel" as const,
                    id,
                    mode: "kitchen" as const,
                  })),
              ]
              if (changes.length) save.mutate(changes)
            }}
          />
        </SettingsPanel>
      </SettingsSection>
    </>
  )
}
