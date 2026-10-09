import { CheckIcon } from "@langchain/macaw-components/icons"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { Switch } from "@langchain/macaw-components/Switch"
import { Text } from "@langchain/macaw-components/Text"
import { ArrowsClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowsClockwise"
import { BroadcastIcon } from "@phosphor-icons/react/dist/ssr/Broadcast"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"
import { useState } from "react"
import type { ReactNode } from "react"

import {
  SettingsPanel,
  SettingsRow,
  SettingsSection,
} from "@/components/AppShell"
import { SlackChannelMultiCombobox } from "@/components/SlackChannelCombobox"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { incidentsApi } from "./api"
import type { IncidentPolicy, IncidentSettingsPayload } from "./api"
import { ErrorState, formatTime, LoadingState } from "./shared"

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return (
    <div role={error ? "alert" : "status"}>
      <Banner intent={error ? "error" : "info"}>{children}</Banner>
    </div>
  )
}

function PolicyForm({
  settings,
  onReload,
}: {
  settings: IncidentSettingsPayload
  onReload: () => void
}) {
  const queryClient = useQueryClient()
  const [initial] = useState(settings.policy)
  const [enabled, setEnabled] = useState(initial.enabled)
  const [channelPrefix, setChannelPrefix] = useState(initial.channel_prefix)
  const [excludedChannelIds, setExcludedChannelIds] = useState(
    initial.excluded_channel_ids
  )
  const [model, setModel] = useState(initial.model ?? "")
  const [maxModelCalls, setMaxModelCalls] = useState(
    String(initial.max_model_calls)
  )
  const [validation, setValidation] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: incidentsApi.saveSettings,
    meta: { silent: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["incidents", "settings"],
      })
    },
  })
  const operation = settings.last_operation
  const submittedOperation =
    operation?.command_id === save.data?.command_id ? operation : null
  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const policy: IncidentPolicy = {
      ...initial,
      enabled,
      channel_prefix: channelPrefix.trim(),
      excluded_channel_ids: excludedChannelIds,
      model: model.trim() || null,
      max_model_calls: Number(maxModelCalls),
    }
    if (!/^[a-z0-9_-]+$/.test(policy.channel_prefix)) {
      setValidation(
        "Use a nonempty channel prefix with lowercase letters, numbers, hyphens, or underscores."
      )
      return
    }
    setValidation(null)
    save.mutate(policy)
  }
  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <SettingsSection
        title="Incidents"
        description="Investigate matching Slack channels and maintain incident summaries with Open SWE."
      >
        <SlackConnectionStatus settings={settings} />
        <SettingsRow
          label="Enable Incidents"
          htmlFor="policy-enabled"
          control={
            <Switch
              id="policy-enabled"
              aria-label="Enable Incidents"
              checked={enabled}
              onChange={setEnabled}
            />
          }
        />
        <SettingsPanel>
          <div className="grid gap-space-5 sm:grid-cols-2">
            <Input
              size="md"
              id="policy-channel_prefix"
              label="Channel prefix"
              value={channelPrefix}
              onChange={setChannelPrefix}
              placeholder="inc-"
              maxLength={60}
              pattern="[a-z0-9_-]+"
            />
            <div className="space-y-space-1">
              <Text as="span" variant="sm" weight="medium" className="block">
                Excluded channels
              </Text>
              <SlackChannelMultiCombobox
                value={excludedChannelIds}
                onValueChange={setExcludedChannelIds}
                aria-label="Excluded channels"
                className="min-h-9 bg-surface-level-1"
              />
            </div>
          </div>
        </SettingsPanel>
        <details>
          <summary className="cursor-pointer px-4 py-3 text-xs font-medium">
            Model and analysis limits
          </summary>
          <SettingsPanel>
            <div className="grid gap-x-5 gap-y-6 sm:grid-cols-2">
              <Input
                size="md"
                type="number"
                id="policy-max_model_calls"
                label="Model calls per turn"
                value={maxModelCalls}
                onChange={setMaxModelCalls}
                min={1}
                max={20}
                step={1}
                required
              />
              <Input
                size="md"
                id="policy-model"
                label="Model"
                value={model}
                onChange={setModel}
                placeholder="Server default"
              />
            </div>
          </SettingsPanel>
        </details>
      </SettingsSection>
      {validation && <Notice error>{validation}</Notice>}
      {save.error && <Notice error>{save.error.message}</Notice>}
      {operation?.status === "failed" && (
        <Notice error>
          {operation.error || "The settings operation failed."}
        </Notice>
      )}
      {save.isSuccess && (
        <Notice>
          {submittedOperation?.status === "applied"
            ? "Settings applied."
            : "Settings update requested. The policy becomes effective after server validation."}
        </Notice>
      )}
      <div className="flex flex-wrap items-center justify-between gap-4 border-t border-default pt-5">
        <p className="max-w-md text-xs leading-relaxed text-secondary">
          Policy version {initial.version}. Existing matching channels are not
          enrolled unless a new matching rename event is received.
        </p>
        <div className="flex gap-2">
          <Button
            color="secondary"
            variant="outlined"
            leftDecorator={ArrowsClockwiseIcon}
            onClick={onReload}
            disabled={save.isPending}
          >
            Reload settings
          </Button>
          <Button
            type="submit"
            leftDecorator={CheckIcon}
            loading={save.isPending}
            disabled={
              save.isPending ||
              (save.isSuccess && submittedOperation?.status !== "failed")
            }
          >
            Save settings
          </Button>
        </div>
      </div>
    </form>
  )
}

function SlackConnectionStatus({
  settings,
}: {
  settings: IncidentSettingsPayload
}) {
  const { connection, policy } = settings
  const connectionError =
    connection.error ||
    (!connection.slack_configured
      ? "Slack is not configured. Connect the workspace before enabling Incidents."
      : connection.required_scopes_present === false
        ? "Required Slack scopes are missing."
        : null)
  return (
    <div className="px-4 py-4">
      <div className="flex items-start gap-3">
        {connectionError ? (
          <WarningCircleIcon
            size={20}
            weight="regular"
            className="mt-0.5 shrink-0 text-icon-warning"
          />
        ) : (
          <BroadcastIcon
            size={20}
            weight="regular"
            className="mt-0.5 shrink-0 text-icon-brand"
          />
        )}
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-medium text-primary">
            {connectionError
              ? "Slack connection needs attention"
              : "Slack connection configured"}
          </h2>
          {connectionError && (
            <p className="mt-1 text-xs leading-relaxed text-secondary">
              {connectionError}
            </p>
          )}
          <dl className="mt-4 grid grid-cols-1 gap-3 text-xs sm:grid-cols-3">
            <div>
              <dt className="text-secondary">Workspace</dt>
              <dd className="mt-1 font-mono">
                {connection.workspace_id ||
                  policy.workspace_id ||
                  "Not configured"}
              </dd>
            </div>
            <div>
              <dt className="text-secondary">Slack app</dt>
              <dd className="mt-1 font-mono">
                {connection.slack_app_id ||
                  policy.slack_app_id ||
                  "Not configured"}
              </dd>
            </div>
            <div>
              <dt className="text-secondary">Last verified</dt>
              <dd className="mt-1">{formatTime(connection.verified_at)}</dd>
            </div>
          </dl>
        </div>
      </div>
    </div>
  )
}

export function IncidentSettings() {
  const [formVersion, setFormVersion] = useState(0)
  const settings = useQuery({
    queryKey: ["incidents", "settings"],
    queryFn: incidentsApi.settings,
    retry: false,
    meta: { invalidatedBy: [invalidationTopic("incident-settings")] },
  })
  if (settings.isPending) return <LoadingState />
  if (settings.error)
    return (
      <div className="p-6">
        <ErrorState
          error={settings.error}
          retry={() => void settings.refetch()}
        />
      </div>
    )
  return (
    <div>
      <PolicyForm
        key={formVersion}
        settings={settings.data}
        onReload={() => {
          void settings.refetch().then((result) => {
            if (result.isSuccess) setFormVersion((version) => version + 1)
          })
        }}
      />
    </div>
  )
}
