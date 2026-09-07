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
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { investigateApi } from "./api"
import type { InvestigationPolicy, InvestigationSettingsPayload } from "./api"
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
        className="block text-xs font-medium"
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
          className="min-h-20 bg-background"
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
          className="h-9 bg-background"
        />
      )}
      {description && (
        <p
          id={`policy-${name}-description`}
          className="text-xs leading-relaxed text-muted-foreground"
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
      className={`rounded-lg border p-3 text-sm ${error ? "border-destructive/20 bg-destructive/5 text-destructive-foreground" : "border-info/20 bg-info/5 text-info-foreground"}`}
    >
      {children}
    </div>
  )
}

function PolicyForm({
  settings,
  onReload,
}: {
  settings: InvestigationSettingsPayload
  onReload: () => void
}) {
  const queryClient = useQueryClient()
  const [initial] = useState(settings.policy)
  const [validation, setValidation] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: investigateApi.saveSettings,
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["investigate", "settings"],
      })
    },
  })
  const operation = settings.last_operation
  const submittedOperation =
    operation?.command_id === save.data?.command_id ? operation : null
  const list = (data: FormData, key: string) => [
    ...new Set(
      String(data.get(key) ?? "")
        .split(/[,\n]/)
        .map((entry) => entry.trim())
        .filter(Boolean)
    ),
  ]
  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const policy: InvestigationPolicy = {
      ...initial,
      enabled: form.has("enabled"),
      channel_prefix: String(form.get("channel_prefix") ?? "").trim(),
      excluded_channel_ids: list(form, "excluded_channel_ids"),
      model: String(form.get("model") ?? "").trim() || null,
      max_model_calls: Number(form.get("max_model_calls")),
      max_pass_seconds: Number(form.get("max_pass_seconds")),
      idle_timeout_seconds: Number(form.get("idle_timeout_seconds")),
      max_watch_seconds: Number(form.get("max_watch_seconds")),
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
    <form onSubmit={onSubmit} className="space-y-8">
      <SettingsSection title="Automatic investigation">
        <SettingsRow
          label="Enable Investigate"
          htmlFor="policy-enabled"
          control={
            <Toggle
              name="enabled"
              label="Enable Investigate"
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
            <Field
              name="excluded_channel_ids"
              label="Excluded channel IDs"
              value={initial.excluded_channel_ids.join(", ")}
              placeholder="C0123456789"
            />
          </div>
        </SettingsPanel>
      </SettingsSection>
      <SettingsSection title="Investigation limits">
        <SettingsPanel>
          <div className="grid gap-x-5 gap-y-6 sm:grid-cols-2">
            {[
              {
                name: "max_model_calls",
                label: "Model calls per pass",
                value: initial.max_model_calls,
                min: 1,
                max: 20,
              },
              {
                name: "max_pass_seconds",
                label: "Pass timeout (seconds)",
                value: initial.max_pass_seconds,
                min: 10,
                max: 300,
              },
              {
                name: "idle_timeout_seconds",
                label: "Idle timeout (seconds)",
                value: initial.idle_timeout_seconds,
                min: 60,
                max: 86400,
              },
              {
                name: "max_watch_seconds",
                label: "Maximum watch duration (seconds)",
                value: initial.max_watch_seconds,
                min: 60,
                max: 86400,
              },
            ].map(({ name, label, value, min, max }) => (
              <label
                key={name}
                htmlFor={`policy-${name}`}
                className="space-y-2"
              >
                <span className="block text-xs font-medium">{label}</span>
                <Input
                  type="number"
                  min={min}
                  max={max}
                  step={1}
                  required
                  id={`policy-${name}`}
                  name={name}
                  defaultValue={value}
                  className="h-9 bg-background"
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
      <div className="flex flex-wrap items-center justify-between gap-4 border-t border-border pt-5">
        <p className="max-w-md text-xs leading-relaxed text-muted-foreground">
          Policy version {initial.version}. Existing matching channels are not
          enrolled unless a new matching rename event is received.
        </p>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onReload}
            disabled={save.isPending}
          >
            <RefreshCw className="size-3.5" />
            Reload settings
          </Button>
          <Button
            type="submit"
            size="sm"
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

export function InvestigationSettings() {
  const [formVersion, setFormVersion] = useState(0)
  const settings = useQuery({
    queryKey: ["investigate", "settings"],
    queryFn: investigateApi.settings,
    retry: false,
    refetchInterval: 5000,
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
  const { connection, policy } = settings.data
  const connectionError =
    connection.error ||
    (!connection.slack_configured
      ? "Slack is not configured. Connect the workspace before enabling Investigate."
      : connection.required_scopes_present === false
        ? "Required Slack scopes are missing."
        : null)
  return (
    <div className="mx-auto w-full max-w-4xl px-5 py-8 sm:px-10 sm:py-10">
      <header className="mb-8">
        <div className="mb-2 text-xs text-muted-foreground">
          Investigate / Settings
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Investigation policy
        </h1>
      </header>
      <div className="mb-8 rounded-xl border border-border bg-card p-5">
        <div className="flex items-start gap-3">
          {connectionError ? (
            <CircleAlert className="mt-0.5 size-5 text-warning-foreground" />
          ) : (
            <Radio className="mt-0.5 size-5 text-info-foreground" />
          )}
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-medium">
              {connectionError
                ? "Slack connection needs attention"
                : "Slack connection configured"}
            </h2>
            {connectionError && (
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {connectionError}
              </p>
            )}
            <dl className="mt-4 grid grid-cols-1 gap-3 text-xs sm:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">Workspace</dt>
                <dd className="mt-1 font-mono">
                  {connection.workspace_id ||
                    policy.workspace_id ||
                    "Not configured"}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Slack app</dt>
                <dd className="mt-1 font-mono">
                  {connection.slack_app_id ||
                    policy.slack_app_id ||
                    "Not configured"}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Last verified</dt>
                <dd className="mt-1">{formatTime(connection.verified_at)}</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
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
