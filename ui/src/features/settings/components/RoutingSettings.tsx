import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { Link as MacawLink } from "@langchain/macaw-components/Link"
import { Select, type SelectOption } from "@langchain/macaw-components/Select"
import { Switch } from "@langchain/macaw-components/Switch"

import { SettingsPanel, SettingsSection } from "@/components/AppShell"
import {
  useWorkspaceOptions,
  workspaceOptionKeys,
} from "@/features/agents/lib/queries"
import {
  api,
  type RepositorySettings,
  type WorkspaceOptionList,
  type WorkspaceRecord,
  type WorkspaceUpdate,
} from "@/lib/api"
import {
  slackChannelHref,
  slackChannelLabel,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import { cn, INHERITED_LINK } from "@/lib/utils"
import { RepositoryPicker, SlackChannelPicker } from "./WorkspaceBindingPickers"

type Bindings = Pick<
  WorkspaceRecord,
  "repos" | "slack_channel_ids" | "kitchen_channel_ids" | "ask_channel_ids"
>
type RoutingChange = { slug: string } & (
  | { update: (record: Bindings) => WorkspaceUpdate }
  | { repo: string; mayStartThreads: boolean }
)

const CHANNEL_MODES: Array<SelectOption<"mentions" | "kitchen" | "ask">> = [
  { value: "mentions", label: "Mention-only" },
  { value: "kitchen", label: "Kitchen" },
  { value: "ask", label: "Ask" },
]

export function RoutingSettings() {
  const qc = useQueryClient()
  const options = useWorkspaceOptions()
  const records = options.data?.workspaces ?? []
  const defaultRecord = records.find((record) => record.is_default)
  const directory = useSlackChannelDirectory(true)
  const permissions = useQueries({
    queries: records.map((record) => ({
      queryKey: ["workspaceRepositories", record.slug],
      queryFn: () => api.listWorkspaceRepositories(record.slug),
    })),
  })
  const save = useMutation({
    mutationKey: ["routingBindings", "save"],
    scope: { id: "routingBindings" },
    meta: { errorTitle: "Couldn't save routing settings" },
    mutationFn: async (changes: Array<RoutingChange>) => {
      for (const change of changes) {
        if ("update" in change) {
          const current = await api.getWorkspace(change.slug)
          await api.updateWorkspace(change.slug, change.update(current))
        } else {
          await api.configureWorkspaceRepository(change.slug, change.repo, {
            may_start_threads: change.mayStartThreads,
          })
        }
      }
    },
    onMutate: async (changes) => {
      await qc.cancelQueries({ queryKey: workspaceOptionKeys.all })
      await qc.cancelQueries({ queryKey: ["workspaceRepositories"] })
      const previous = qc.getQueryData<WorkspaceOptionList>(
        workspaceOptionKeys.all
      )
      const previousPermissions = qc.getQueriesData<Array<RepositorySettings>>({
        queryKey: ["workspaceRepositories"],
      })
      qc.setQueryData<WorkspaceOptionList>(
        workspaceOptionKeys.all,
        (current) =>
          current && {
            ...current,
            workspaces: current.workspaces.map((record) => {
              let updated = record
              for (const change of changes) {
                if (change.slug === record.slug && "update" in change)
                  updated = { ...updated, ...change.update(updated) }
              }
              return updated
            }),
          }
      )
      for (const change of changes) {
        if ("repo" in change)
          qc.setQueryData<Array<RepositorySettings>>(
            ["workspaceRepositories", change.slug],
            (rows) =>
              rows?.map((row) =>
                row.repo === change.repo
                  ? { ...row, may_start_threads: change.mayStartThreads }
                  : row
              )
          )
      }
      return { previous, previousPermissions }
    },
    onError: (_error, _changes, context) => {
      if (!context) return
      qc.setQueryData(workspaceOptionKeys.all, context.previous)
      for (const [key, rows] of context.previousPermissions)
        qc.setQueryData(key, rows)
    },
    onSettled: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: workspaceOptionKeys.all }),
        qc.invalidateQueries({ queryKey: ["workspaceRepositories"] }),
        qc.invalidateQueries({ queryKey: ["workspace"] }),
      ])
    },
  })
  const repositoryRows = records
    .flatMap((record, index) =>
      record.repos.map((repo) => ({
        repo,
        slug: record.slug,
        permissions: permissions[index],
        allowed: permissions[index]?.data?.find(
          (row) => row.repo.toLowerCase() === repo.toLowerCase()
        )?.may_start_threads,
      }))
    )
    .sort((a, b) => a.repo.localeCompare(b.repo))
  const selectedRepos = repositoryRows.map((row) => row.repo)
  const channelRows = records.flatMap((record) =>
    record.slack_channel_ids.map((id) => ({ id, record }))
  )
  const channelIds = channelRows.map((row) => row.id)
  const disabled = save.isPending || !options.data || !defaultRecord

  if (!options.data)
    return (
      <SettingsSection title="Routing">
        <SettingsPanel>
          {options.isError ? (
            <>
              <p role="alert" className="text-sm text-error-secondary">
                Couldn't load routing settings.
              </p>
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                onClick={() => void options.refetch()}
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
          {!defaultRecord && (
            <p role="alert" className="text-sm text-error-secondary">
              The default routing configuration is unavailable. Contact an
              administrator.
            </p>
          )}
          {save.error && (
            <p role="alert" className="text-sm text-error-secondary">
              {save.error.message}
            </p>
          )}
          {repositoryRows.length === 0 ? (
            <p className="text-sm text-secondary">No bound repositories.</p>
          ) : (
            <ul className="divide-y divide-default">
              {repositoryRows.map((row) => (
                <li
                  key={row.repo.toLowerCase()}
                  className="flex flex-wrap items-center justify-between gap-space-3 py-space-2"
                >
                  <span className="min-w-0 font-mono text-xs break-all text-primary">
                    {row.repo}
                  </span>
                  <div className="flex items-center gap-space-2">
                    <label className="flex items-center gap-space-2 text-xs text-secondary">
                      Can start threads
                      <Switch
                        aria-label={`Let ${row.repo} start threads`}
                        checked={row.allowed === true}
                        disabled={disabled || row.allowed === undefined}
                        onChange={(mayStartThreads) =>
                          save.mutate([
                            { slug: row.slug, repo: row.repo, mayStartThreads },
                          ])
                        }
                      />
                    </label>
                    {row.permissions?.isError && (
                      <Button
                        size="xs"
                        color="secondary"
                        variant="plain"
                        disabled={save.isPending}
                        onClick={() => void row.permissions?.refetch()}
                      >
                        Retry permissions
                      </Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <RepositoryPicker
            selected={selectedRepos}
            disabled={disabled}
            onChange={(next) => {
              if (!defaultRecord) return
              const desired = new Set(next.map((repo) => repo.toLowerCase()))
              const known = new Set(
                selectedRepos.map((repo) => repo.toLowerCase())
              )
              const added = next.filter(
                (repo) => !known.has(repo.toLowerCase())
              )
              const changes: Array<RoutingChange> = records.flatMap(
                (record) => {
                  const removed = new Set(
                    record.repos
                      .filter((repo) => !desired.has(repo.toLowerCase()))
                      .map((repo) => repo.toLowerCase())
                  )
                  const additions =
                    record.slug === defaultRecord.slug ? added : []
                  return removed.size || additions.length
                    ? [
                        {
                          slug: record.slug,
                          update: (current: Bindings) => ({
                            repos: [
                              ...new Set([
                                ...current.repos.filter(
                                  (repo) => !removed.has(repo.toLowerCase())
                                ),
                                ...additions,
                              ]),
                            ],
                          }),
                        },
                      ]
                    : []
                }
              )
              if (changes.length) save.mutate(changes)
            }}
          />
        </SettingsPanel>
      </SettingsSection>
      <SettingsSection
        title="Slack channels"
        description="Mention-only requires an @mention. Kitchen and Ask receive every human message without a mention; Ask investigates reports and proposes next steps. Kitchen and Ask require an internal channel Robin has joined. Personal kitchen preferences remain separate."
      >
        <SettingsPanel>
          {channelRows.length === 0 ? (
            <p className="text-sm text-secondary">No bound channels.</p>
          ) : (
            <ul className="divide-y divide-default">
              {channelRows.map(({ id, record }) => {
                const label = slackChannelLabel(directory.data, id, channelIds)
                return (
                  <li
                    key={id}
                    className="flex items-center justify-between gap-space-3 py-space-2"
                  >
                    <MacawLink
                      variant="xs"
                      href={slackChannelHref(id)}
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
                      value={
                        record.ask_channel_ids?.includes(id)
                          ? "ask"
                          : record.kitchen_channel_ids.includes(id)
                            ? "kitchen"
                            : "mentions"
                      }
                      options={CHANNEL_MODES}
                      disabled={disabled}
                      onChange={(mode) => {
                        if (!mode) return
                        save.mutate([
                          {
                            slug: record.slug,
                            update: (current) => ({
                              kitchen_channel_ids: [
                                ...current.kitchen_channel_ids.filter(
                                  (channel) => channel !== id
                                ),
                                ...(mode === "kitchen" ? [id] : []),
                              ],
                              ask_channel_ids: [
                                ...(current.ask_channel_ids ?? []).filter(
                                  (channel) => channel !== id
                                ),
                                ...(mode === "ask" ? [id] : []),
                              ],
                            }),
                          },
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
              if (!defaultRecord) return
              const desired = new Set(next)
              const known = new Set(channelIds)
              const added = next.filter((id) => !known.has(id))
              const changes: Array<RoutingChange> = records.flatMap(
                (record) => {
                  const removed = new Set(
                    record.slack_channel_ids.filter((id) => !desired.has(id))
                  )
                  const additions =
                    record.slug === defaultRecord.slug ? added : []
                  return removed.size || additions.length
                    ? [
                        {
                          slug: record.slug,
                          update: (current: Bindings) => ({
                            slack_channel_ids: [
                              ...new Set([
                                ...current.slack_channel_ids.filter(
                                  (id) => !removed.has(id)
                                ),
                                ...additions,
                              ]),
                            ],
                            kitchen_channel_ids:
                              current.kitchen_channel_ids.filter(
                                (id) => !removed.has(id)
                              ),
                            ask_channel_ids: (
                              current.ask_channel_ids ?? []
                            ).filter((id) => !removed.has(id)),
                          }),
                        },
                      ]
                    : []
                }
              )
              if (changes.length) save.mutate(changes)
            }}
          />
        </SettingsPanel>
      </SettingsSection>
    </>
  )
}
