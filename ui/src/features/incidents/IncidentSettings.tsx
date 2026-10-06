import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Check,
  CircleAlert,
  LoaderCircle,
  Radio,
  RefreshCw,
} from "lucide-react"
import { useState } from "react"
import type { ReactNode } from "react"

import {
  SettingsPanel,
  SettingsRow,
  SettingsSection,
} from "@/components/AppShell"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
import { SlackChannelMultiCombobox } from "@/components/SlackChannelCombobox"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { incidentsApi } from "./api"
import type { IncidentPolicy, IncidentSettingsPayload } from "./api"
import { ErrorState, formatTime, LoadingState } from "./shared"

function Field({
  name,
  label,
  description,
  value,
  multiline,
  placeholder,
}: {
  name: string
  label: string
  description?: string
  value: string
  multiline?: boolean
  placeholder?: string
}) {
  const describedBy = description ? `policy-${name}-description` : undefined
  return (
    <div className="space-y-2">
      <label
        id={`policy-${name}-label`}
        htmlFor={`policy-${name}`}
        className="block text-label font-medium"
      >
        {label}
      </label>
      {multiline ? (
        <Textarea
          id={`policy-${name}`}
          name={name}
          aria-describedby={describedBy}
          defaultValue={value}
          placeholder={placeholder}
          className="min-h-20 bg-canvas"
        />
      ) : (
        <Input
          id={`policy-${name}`}
          name={name}
          aria-describedby={describedBy}
          defaultValue={value}
          placeholder={placeholder}
          required={name === "channel_prefix"}
          maxLength={name === "channel_prefix" ? 60 : undefined}
          pattern={name === "channel_prefix" ? "[a-z0-9_-]+" : undefined}
          className="h-9 bg-canvas"
        />
      )}
      {description && (
        <p
          id={`policy-${name}-description`}
          className="text-meta leading-relaxed text-ink-subtle"
        >
          {description}
        </p>
      )}
    </div>
  )
}

function Toggle({
  name,
  label,
  checked,
}: {
  name: string
  label: string
  checked: boolean
}) {
  return (
    <Switch
      id={`policy-${name}`}
      name={name}
      aria-label={label}
      defaultChecked={checked}
    />
  )
}

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return (
    <div
      role={error ? "alert" : "status"}
      className={`rounded-compact border p-3 text-body ${error ? "border-risk/20 bg-risk-bg text-risk" : "border-info/20 bg-info/5 text-info"}`}
    >
      {children}
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
            <Toggle
              name="enabled"
              label="Enable Incidents"
              checked={initial.enabled}
            />
          }
        />
        <SettingsPanel>
          <div className="grid gap-5 sm:grid-cols-2">
            <Field
              name="channel_prefix"
              label="Channel prefix"
              value={initial.channel_prefix}
              placeholder="inc-"
            />
            <div className="space-y-2">
              <span className="block text-label font-medium">
                Excluded channels
              </span>
              <SlackChannelMultiCombobox
                value={excludedChannelIds}
                onValueChange={setExcludedChannelIds}
                aria-label="Excluded channels"
                className="min-h-9 bg-canvas"
              />
            </div>
          </div>
        </SettingsPanel>
        <details>
          <summary className="cursor-pointer px-4 py-3 text-label font-medium">
            Model and analysis limits
          </summary>
          <SettingsPanel>
            <div className="grid gap-x-5 gap-y-6 sm:grid-cols-2">
              {[
                {
                  name: "max_model_calls",
                  label: "Model calls per turn",
                  value: initial.max_model_calls,
                  min: 1,
                  max: 20,
                },
              ].map(({ name, label, value, min, max }) => (
                <label
                  key={name}
                  htmlFor={`policy-${name}`}
                  className="space-y-2"
                >
                  <span className="block text-label font-medium">{label}</span>
                  <Input
                    type="number"
                    min={min}
                    max={max}
                    step={1}
                    required
                    id={`policy-${name}`}
                    name={name}
                    defaultValue={value}
                    className="h-9 bg-canvas"
                  />
                </label>
              ))}
              <Field
                name="model"
                label="Model"
                value={initial.model ?? ""}
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
      <div className="flex flex-wrap items-center justify-between gap-4 border-t border-line pt-5">
        <p className="max-w-md text-meta leading-relaxed text-ink-subtle">
          Policy version {initial.version}. Existing matching channels are not
          enrolled unless a new matching rename event is received.
        </p>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="compact"
            onClick={onReload}
            disabled={save.isPending}
          >
            <RefreshCw className="size-3.5" />
            Reload settings
          </Button>
          <Button
            type="submit"
            size="compact"
            disabled={
              save.isPending ||
              (save.isSuccess && submittedOperation?.status !== "failed")
            }
          >
            {save.isPending ? (
              <LoaderCircle className="size-3.5 animate-spin" />
            ) : (
              <Check className="size-3.5" />
            )}
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
          <CircleAlert className="mt-0.5 size-5 text-attention" />
        ) : (
          <Radio className="mt-0.5 size-5 text-info" />
        )}
        <div className="min-w-0 flex-1">
          <h2 className="text-body font-medium">
            {connectionError
              ? "Slack connection needs attention"
              : "Slack connection configured"}
          </h2>
          {connectionError && (
            <p className="mt-1 text-meta leading-relaxed text-ink-subtle">
              {connectionError}
            </p>
          )}
          <dl className="mt-4 grid grid-cols-1 gap-3 text-label sm:grid-cols-3">
            <div>
              <dt className="text-ink-subtle">Workspace</dt>
              <dd className="mt-1 font-mono">
                {connection.workspace_id ||
                  policy.workspace_id ||
                  "Not configured"}
              </dd>
            </div>
            <div>
              <dt className="text-ink-subtle">Slack app</dt>
              <dd className="mt-1 font-mono">
                {connection.slack_app_id ||
                  policy.slack_app_id ||
                  "Not configured"}
              </dd>
            </div>
            <div>
              <dt className="text-ink-subtle">Last verified</dt>
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
