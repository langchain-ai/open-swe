import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import {
  SettingRow,
  SettingSection,
} from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Alert, AlertDescription } from "@langchain/gtm-platform-design-system/ui/alert"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"

import { AlertTriangle, Check, Info, RefreshCw } from "@/components/glyphs"
import { SlackChannelMultiCombobox } from "@/components/SlackChannelCombobox"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { incidentsApi } from "./api"
import type { IncidentPolicy, IncidentSettingsPayload } from "./api"
import { ErrorState, formatTime, LoadingState } from "./shared"

function SaveFailure({ title, reason }: { title: string; reason: string }) {
  return (
    <StateNotice
      tone="RISK"
      icon={AlertTriangle}
      title={title}
      description={reason}
    />
  )
}

/*
 * The policy is one versioned document (`expected_version`), so it keeps an
 * explicit Save: per-row save-on-change would race its own version bumps.
 */
function PolicyForm({
  settings,
  onReload,
}: {
  settings: IncidentSettingsPayload
  onReload: () => void
}) {
  const queryClient = useQueryClient()
  const [initial] = useState(settings.policy)
  const [excludedChannelIds, setExcludedChannelIds] = useState(
    settings.policy.excluded_channel_ids
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
    const form = new FormData(event.currentTarget)
    const policy: IncidentPolicy = {
      ...initial,
      enabled: form.has("enabled"),
      channel_prefix: String(form.get("channel_prefix") ?? "").trim(),
      excluded_channel_ids: excludedChannelIds,
      model: String(form.get("model") ?? "").trim() || null,
      max_model_calls: Number(form.get("max_model_calls")),
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
    <Stack render={<form onSubmit={onSubmit} />} gap="2xl">
      <SlackConnectionStatus settings={settings} />
      <SettingSection
        title="Incidents"
        description="Investigate matching Slack channels and maintain incident summaries with Open SWE."
      >
        <SettingRow
          label="Enable Incidents"
          control={({ id }) => (
            <Switch
              id={id}
              name="enabled"
              aria-label="Enable Incidents"
              defaultChecked={initial.enabled}
            />
          )}
        />
        <SettingRow
          label="Channel prefix"
          description="New public channels with this prefix are investigated."
          control={({ id }) => (
            <Input
              id={id}
              name="channel_prefix"
              defaultValue={initial.channel_prefix}
              placeholder="inc-"
              required
              maxLength={60}
              pattern="[a-z0-9_-]+"
            />
          )}
        />
        <SettingRow
          label="Excluded channels"
          description="Matching channels Open SWE leaves alone."
          control={() => (
            <SlackChannelMultiCombobox
              value={excludedChannelIds}
              onValueChange={setExcludedChannelIds}
              aria-label="Excluded channels"
              className="w-full"
            />
          )}
        />
      </SettingSection>
      <SettingSection
        title="Model and analysis limits"
      >
        <SettingRow
          label="Model calls per turn"
          control={({ id }) => (
            <Input
              type="number"
              min={1}
              max={20}
              step={1}
              required
              id={id}
              name="max_model_calls"
              defaultValue={initial.max_model_calls}
            />
          )}
        />
        <SettingRow
          label="Model"
          control={({ id }) => (
            <Input
              id={id}
              name="model"
              defaultValue={initial.model ?? ""}
              placeholder="Server default"
            />
          )}
        />
      </SettingSection>
      {validation && (
        <SaveFailure title="Settings were not saved" reason={validation} />
      )}
      {save.error && (
        <SaveFailure
          title="Settings were not saved"
          reason={save.error.message}
        />
      )}
      {operation?.status === "failed" && (
        <SaveFailure
          title="The last settings change failed"
          reason={operation.error || "The settings operation failed."}
        />
      )}
      {save.isSuccess && (
        <Alert
          tone={submittedOperation?.status === "applied" ? "positive" : "info"}
          icon={submittedOperation?.status === "applied" ? Check : Info}
          role="status"
        >
          <AlertDescription>
            {submittedOperation?.status === "applied"
              ? "Settings applied."
              : "Settings update requested. The policy becomes effective after server validation."}
          </AlertDescription>
        </Alert>
      )}
      <Inline
        gap="lg"
        justify="between"
        wrap
        className="border-t border-line pt-6"
      >
        <Box render={<p />} className="max-w-md text-meta text-ink-subtle">
          Policy version {initial.version}. Existing matching channels are not
          enrolled unless a new matching rename event is received.
        </Box>
        <Inline gap="sm">
          <Button
            type="button"
            variant="outline"
            onClick={onReload}
            disabled={save.isPending}
          >
            <Icon icon={RefreshCw} size="sm" />
            Reload settings
          </Button>
          <Button
            type="submit"
            disabled={
              save.isPending ||
              (save.isSuccess && submittedOperation?.status !== "failed")
            }
          >
            {save.isPending ? (
              <Spinner size="sm" />
            ) : (
              <Icon icon={Check} size="sm" />
            )}
            Save settings
          </Button>
        </Inline>
      </Inline>
    </Stack>
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
  const facts = [
    {
      label: "Workspace",
      value:
        connection.workspace_id || policy.workspace_id || "Not configured",
      mono: true,
    },
    {
      label: "Slack app",
      value: connection.slack_app_id || policy.slack_app_id || "Not configured",
      mono: true,
    },
    {
      label: "Last verified",
      value: formatTime(connection.verified_at),
      mono: false,
    },
  ]
  return (
    <PageSection
      title="Slack connection"
      description={
        connectionError ? undefined : "Slack connection configured."
      }
    >
      <Stack gap="lg">
        {connectionError && (
          <StateNotice
            tone="ATTENTION"
            icon={AlertTriangle}
            title="Slack connection needs attention"
            description={connectionError}
          />
        )}
        <Box
          render={<dl />}
          className="grid grid-cols-1 gap-3 text-label sm:grid-cols-3"
        >
          {facts.map((fact) => (
            <Stack key={fact.label} gap="xs">
              <Box render={<dt />} className="text-meta text-ink-subtle">
                {fact.label}
              </Box>
              <Box
                render={<dd />}
                className={fact.mono ? "font-mono text-ink" : "text-ink"}
              >
                {fact.value}
              </Box>
            </Stack>
          ))}
        </Box>
      </Stack>
    </PageSection>
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
      <ErrorState
        error={settings.error}
        retry={() => void settings.refetch()}
      />
    )
  return (
    <PolicyForm
      key={formVersion}
      settings={settings.data}
      onReload={() => {
        void settings.refetch().then((result) => {
          if (result.isSuccess) setFormVersion((version) => version + 1)
        })
      }}
    />
  )
}
