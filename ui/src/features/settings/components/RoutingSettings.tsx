import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { Banner } from "@langchain/macaw-components/Banner"
import { EmptyState } from "@langchain/macaw-components/EmptyState"

import { SettingsPanel, SettingsSection } from "@/components/AppShell"
import { api, type RoutingSettings as RoutingData } from "@/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { HashIcon } from "@phosphor-icons/react/dist/ssr/Hash"
import { RefreshSlackChannels } from "@/components/SlackChannelCombobox"
import {
  slackChannelLabel,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import { RepositoryPicker } from "./RoutingBindingPickers"
import { AddListeningChannel } from "./AddListeningChannel"
import { ListeningChannelRow } from "./ListeningChannelRow"
import { ListeningPatternRow, patternMatches } from "./ListeningPatternRow"

type RoutingChange =
  | { kind: "repository"; repo: string; bound: boolean }
  | { kind: "channel"; id: string; prompt: string | null }
  | {
      kind: "rule"
      prefix: string
      instructions: { prompt: string; kickoff: string } | null
    }

const QUERY_KEY = ["routing"]

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
          if (change.bound) await api.configureRoutingRepository(change.repo)
          else await api.removeRoutingRepository(change.repo)
        } else if (change.kind === "rule") {
          if (change.instructions === null)
            await api.removeRoutingChannelRule(change.prefix)
          else
            await api.configureRoutingChannelRule(
              change.prefix,
              change.instructions.prompt,
              change.instructions.kickoff
            )
        } else if (change.prompt === null)
          await api.removeRoutingChannel(change.id)
        else await api.configureRoutingChannel(change.id, change.prompt)
      }
    },
    onMutate: async (changes) => {
      await qc.cancelQueries({ queryKey: QUERY_KEY })
      const previous = qc.getQueryData<RoutingData>(QUERY_KEY)
      qc.setQueryData<RoutingData>(QUERY_KEY, (current) => {
        if (!current) return current
        let repos = current.repos
        let channels = current.slack_channels
        let rules = current.slack_channel_rules
        for (const change of changes) {
          if (change.kind === "repository") {
            repos = repos.filter(
              (row) => row.repo.toLowerCase() !== change.repo.toLowerCase()
            )
            if (change.bound) repos = [...repos, { repo: change.repo }]
          } else if (change.kind === "rule") {
            rules = rules.filter((row) => row.prefix !== change.prefix)
            if (change.instructions !== null)
              rules = [
                ...rules,
                { prefix: change.prefix, ...change.instructions },
              ]
          } else {
            channels = channels.filter((row) => row.channel_id !== change.id)
            if (change.prompt !== null)
              channels = [
                ...channels,
                { channel_id: change.id, prompt: change.prompt },
              ]
          }
        }
        return {
          ...current,
          repos,
          slack_channels: channels,
          slack_channel_rules: rules,
        }
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
  const channelById = new Map(
    (directory.data?.channels ?? []).map((channel) => [channel.id, channel])
  )
  const attentionFor = (id: string): string | null => {
    if (!directory.data) return null
    const channel = channelById.get(id)
    if (!channel)
      return "Robin can’t find this channel. It may have been archived, or Robin was removed from a private channel."
    if (channel.is_ext_shared)
      return "This channel is shared outside the workspace, so Robin won’t listen here."
    if (!channel.is_member)
      return "Robin isn’t in this channel anymore. Invite Robin in Slack, then refresh channels."
    return null
  }
  const attentionCount = channelIds.filter((id) => attentionFor(id)).length
  const rules = [...(routing.data?.slack_channel_rules ?? [])].sort((a, b) =>
    a.prefix.localeCompare(b.prefix)
  )
  const prefixes = rules.map((rule) => rule.prefix)
  const ruleEditError = (prefix: string) =>
    save.variables?.some(
      (change) =>
        change.kind === "rule" &&
        change.prefix === prefix &&
        change.instructions !== null
    )
      ? save.error?.message
      : undefined

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
        description="Bind repositories for inbound GitHub and Linear routing. Binding is not an access grant or a preload setting."
      >
        <SettingsPanel>
          {save.error &&
            save.variables?.some((change) => change.kind === "repository") && (
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
                  className="py-space-2 font-mono text-xs break-all text-primary"
                >
                  {row.repo}
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
                    bound: false,
                  })),
                ...next
                  .filter((repo) => !known.has(repo.toLowerCase()))
                  .map((repo) => ({
                    kind: "repository" as const,
                    repo,
                    bound: true,
                  })),
              ]
              if (changes.length) save.mutate(changes)
            }}
          />
        </SettingsPanel>
      </SettingsSection>
      <SettingsSection
        title="Slack channels"
        description="Robin reads these channels and can reply without an @mention, following each channel’s instructions."
      >
        <div className="p-space-4">
          <div className="flex items-start justify-between gap-space-4">
            <div className="flex flex-col gap-space-1">
              <h3 className="text-sm font-medium text-primary">
                Listening channels
              </h3>
              <p className="text-xs/relaxed text-secondary">
                {channels.length === 0 && rules.length === 0
                  ? "Not listening anywhere yet."
                  : [
                      channels.length
                        ? `${channels.length} ${channels.length === 1 ? "channel" : "channels"}`
                        : null,
                      rules.length
                        ? `${rules.length} name ${rules.length === 1 ? "pattern" : "patterns"}`
                        : null,
                      attentionCount
                        ? `${attentionCount} ${attentionCount === 1 ? "needs" : "need"} attention`
                        : null,
                    ]
                      .filter((part): part is string => part !== null)
                      .join(" · ")}
              </p>
            </div>
            <AddListeningChannel
              selected={channelIds}
              prefixes={prefixes}
              disabled={disabled}
              error={save.error?.message}
              onSave={(id, prompt, onSuccess) =>
                save.mutate([{ kind: "channel", id, prompt }], { onSuccess })
              }
              onSavePattern={(prefix, prompt, kickoff, onSuccess) =>
                save.mutate(
                  [{ kind: "rule", prefix, instructions: { prompt, kickoff } }],
                  { onSuccess }
                )
              }
            />
          </div>
          {save.error &&
            save.variables?.some(
              (change) =>
                (change.kind === "channel" && change.prompt === null) ||
                (change.kind === "rule" && change.instructions === null)
            ) && (
              <div role="alert" className="mt-space-3">
                <Banner intent="error">{save.error.message}</Banner>
              </div>
            )}
          {channels.length === 0 && rules.length === 0 ? (
            <EmptyState
              size="sm"
              icon={HashIcon}
              title="Robin isn’t listening to any channels."
              description="Pick a channel, or a name prefix like inc-, and a template like Ask or Kitchen. Robin will reply there without an @mention."
              className="mt-space-4 rounded-lg border border-dashed border-default"
            />
          ) : (
            <ul className="mt-space-4 divide-y divide-default rounded-lg border border-default">
              {channels.map((row) => {
                const label = slackChannelLabel(
                  directory.data,
                  row.channel_id,
                  channelIds
                )
                return (
                  <ListeningChannelRow
                    key={row.channel_id}
                    id={row.channel_id}
                    label={label}
                    prompt={row.prompt}
                    channel={channelById.get(row.channel_id)}
                    attention={attentionFor(row.channel_id)}
                    disabled={disabled}
                    error={
                      save.variables?.some(
                        (change) =>
                          change.kind === "channel" &&
                          change.id === row.channel_id &&
                          change.prompt !== null
                      )
                        ? save.error?.message
                        : undefined
                    }
                    onSave={(prompt, onSuccess) =>
                      save.mutate(
                        [{ kind: "channel", id: row.channel_id, prompt }],
                        { onSuccess }
                      )
                    }
                    onRemove={() =>
                      save.mutate([
                        { kind: "channel", id: row.channel_id, prompt: null },
                      ])
                    }
                  />
                )
              })}
              {rules.map((rule) => (
                <ListeningPatternRow
                  key={`pattern:${rule.prefix}`}
                  rule={rule}
                  matches={
                    directory.data
                      ? patternMatches(
                          rule.prefix,
                          directory.data.channels,
                          prefixes,
                          channelIds
                        )
                      : null
                  }
                  disabled={disabled}
                  error={ruleEditError(rule.prefix)}
                  onSave={(prompt, kickoff, onSuccess) =>
                    save.mutate(
                      [
                        {
                          kind: "rule",
                          prefix: rule.prefix,
                          instructions: { prompt, kickoff },
                        },
                      ],
                      { onSuccess }
                    )
                  }
                  onRemove={() =>
                    save.mutate([
                      { kind: "rule", prefix: rule.prefix, instructions: null },
                    ])
                  }
                />
              ))}
            </ul>
          )}
          <div className="mt-space-3 flex flex-col items-start gap-space-2 text-xs text-secondary sm:flex-row sm:items-center sm:justify-between sm:gap-space-3">
            <span>
              Only internal channels Robin has joined can be added. Patterns
              cover public channels and join new matches.
            </span>
            <RefreshSlackChannels
              refresh={directory.refresh}
              isRefreshing={directory.isLoading || directory.isRefreshing}
            />
          </div>
        </div>
      </SettingsSection>
    </>
  )
}
