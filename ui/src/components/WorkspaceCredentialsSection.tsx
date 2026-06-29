import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useMemo, useState } from "react"

import type {
  AIHubImportResult,
  ProjectSecretStatus,
  ProjectSecretUpdateBody,
} from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"

const DEFAULT_ENVIRONMENT = "default"
const AI_HUB_SECRET_NAMES = ["AI_HUB_BASE_URL", "AI_HUB_API_KEY"]

function formatUpdatedAt(value: string | null | undefined): string {
  if (!value) return ""
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

function secretStatus(
  items: Array<ProjectSecretStatus> | undefined,
  projectId: string,
  environment: string,
  name: string
): ProjectSecretStatus {
  return (
    items?.find((item) => item.name === name) ?? {
      connected: false,
      project_id: projectId,
      environment,
      name,
    }
  )
}

function useCredentialInvalidation(projectId: string, environment: string) {
  const qc = useQueryClient()
  return () => {
    void qc.invalidateQueries({
      queryKey: ["projectSecrets", projectId, environment],
    })
    void qc.invalidateQueries({
      queryKey: ["projectAIHubReadiness", projectId, environment],
    })
    void qc.invalidateQueries({
      queryKey: ["deliveryProjectReadiness", projectId],
    })
  }
}

function SecretRow({
  projectId,
  environment,
  status,
  fixed,
}: {
  projectId: string
  environment: string
  status: ProjectSecretStatus
  fixed: boolean
}) {
  const invalidate = useCredentialInvalidation(projectId, environment)
  const [value, setValue] = useState("")
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const onSuccess = () => {
    setValue("")
    setError(null)
    invalidate()
  }
  const onError = (e: Error) => {
    setMessage(null)
    setError(e.message)
  }

  const save = useMutation({
    mutationFn: (body: ProjectSecretUpdateBody) =>
      api.saveProjectSecret(projectId, status.name, body),
    onSuccess,
    onError,
  })
  const test = useMutation({
    mutationFn: () =>
      api.testProjectSecret(projectId, status.name, { environment }),
    onSuccess: (result) => {
      setError(null)
      setMessage(result.ready ? "Validation passed." : "Secret is missing.")
    },
    onError,
  })
  const revoke = useMutation({
    mutationFn: () =>
      api.revokeProjectSecret(projectId, status.name, environment),
    onSuccess,
    onError,
  })

  const connected = status.connected
  const metadata = connected
    ? [
        status.value_last4 ? `value ••••${status.value_last4}` : "",
        status.version ? `v${status.version}` : "",
        formatUpdatedAt(status.updated_at)
          ? `updated ${formatUpdatedAt(status.updated_at)}`
          : "",
      ]
        .filter(Boolean)
        .join(" · ")
    : fixed
      ? "Required for AI Hub readiness in this environment."
      : "Project-scoped secret is not connected in this environment."

  return (
    <SettingsRow
      label={status.name}
      description={metadata}
      control={
        <div className="flex w-full flex-col items-stretch gap-2 sm:w-auto sm:items-end">
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Badge variant={connected ? "default" : "secondary"}>
              {connected ? "Connected" : "Missing"}
            </Badge>
            {connected && (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={`Test ${status.name}`}
                  onClick={() => test.mutate()}
                  disabled={test.isPending}
                >
                  Test
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={`Revoke ${status.name}`}
                  onClick={() => revoke.mutate()}
                  disabled={revoke.isPending}
                >
                  Revoke
                </Button>
              </>
            )}
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              aria-label={`${status.name} value`}
              className="w-full sm:w-72"
              placeholder={connected ? "New value" : "Secret value"}
              type="password"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              disabled={save.isPending}
            />
            <Button
              size="sm"
              aria-label={`${connected ? "Rotate" : "Save"} ${status.name}`}
              onClick={() =>
                save.mutate({
                  environment,
                  value: value.trim(),
                  kind: status.name.startsWith("AI_HUB_")
                    ? "ai_hub_credential"
                    : "api_key",
                })
              }
              disabled={save.isPending || !value.trim()}
            >
              {connected ? "Rotate" : "Save"}
            </Button>
          </div>
          {message && (
            <p className="text-right text-xs text-primary">{message}</p>
          )}
          {error && (
            <p className="text-right text-xs text-destructive">{error}</p>
          )}
        </div>
      }
    />
  )
}

export function WorkspaceCredentialsSection({
  projectId,
}: {
  projectId: string
}) {
  const [environment, setEnvironment] = useState(DEFAULT_ENVIRONMENT)
  const [customName, setCustomName] = useState("")
  const [customValue, setCustomValue] = useState("")
  const [customError, setCustomError] = useState<string | null>(null)
  const [importResult, setImportResult] = useState<AIHubImportResult | null>(
    null
  )
  const invalidate = useCredentialInvalidation(projectId, environment)

  const secrets = useQuery({
    queryKey: ["projectSecrets", projectId, environment],
    queryFn: () => api.listProjectSecrets(projectId, environment),
  })
  const aiHub = useQuery({
    queryKey: ["projectAIHubReadiness", projectId, environment],
    queryFn: () => api.getProjectAIHubReadiness(projectId, environment),
  })
  const importShape = useQuery({
    queryKey: ["projectAIHubImportShape", projectId],
    queryFn: () => api.getProjectAIHubImportShape(projectId),
  })

  const secretNames = useMemo(() => {
    const names = new Set(AI_HUB_SECRET_NAMES)
    for (const item of secrets.data?.items ?? []) names.add(item.name)
    return Array.from(names)
  }, [secrets.data?.items])

  const saveCustom = useMutation({
    mutationFn: () =>
      api.saveProjectSecret(projectId, customName.trim(), {
        environment,
        value: customValue.trim(),
        kind: "api_key",
      }),
    onSuccess: () => {
      setCustomName("")
      setCustomValue("")
      setCustomError(null)
      invalidate()
    },
    onError: (e: Error) => setCustomError(e.message),
  })

  const importAIHub = useMutation({
    mutationFn: () => api.importProjectAIHubSecrets(projectId, { environment }),
    onSuccess: (result) => {
      setImportResult(result)
      setCustomError(null)
      invalidate()
    },
    onError: (e: Error) => {
      setImportResult({
        provider: "ai_hub",
        project_id: projectId,
        environment,
        source_prefix: "",
        imported: [],
        shape: { provider: "ai_hub", candidates: [] },
      })
      setCustomError(e.message)
    },
  })

  const blockers = aiHub.data?.blockers ?? []

  return (
    <SettingsSection
      title="Credentials"
      description="Manage encrypted project secrets for a workspace environment. Saved values are redacted after write and only last four characters are shown."
    >
      <div id="credentials" className="divide-y divide-border">
        <SettingsRow
          label="Environment"
          description="Secrets are scoped by project and environment."
          control={
            <Input
              aria-label="Credentials environment"
              className="w-full sm:w-48"
              value={environment}
              onChange={(e) =>
                setEnvironment(
                  e.target.value.trim().toLowerCase() || DEFAULT_ENVIRONMENT
                )
              }
            />
          }
        />
        <SettingsRow
          label="Provider tokens"
          description="Personal GitHub and Linear tokens are managed per user and required for queue polling, branch delivery, and tracker operations."
          control={
            <a
              className={buttonVariants({ size: "sm", variant: "outline" })}
              href="/my-settings?provider=linear"
            >
              Open Profile Settings
            </a>
          }
        />
        <SettingsRow
          label="AI Hub readiness"
          description={
            blockers.length
              ? blockers
                  .map((blocker) => blocker.message ?? blocker.code)
                  .join(" ")
              : aiHub.data?.ready
                ? "AI Hub credentials are ready for this environment."
                : "Checking AI Hub credentials."
          }
          control={
            <div className="flex flex-col items-end gap-2">
              <Badge variant={aiHub.data?.ready ? "default" : "destructive"}>
                {aiHub.data?.ready ? "Ready" : "Blocked"}
              </Badge>
              <Button
                size="sm"
                variant="outline"
                onClick={() => aiHub.refetch()}
                disabled={aiHub.isFetching}
              >
                Test AI Hub
              </Button>
            </div>
          }
        />
        <SettingsRow
          label="Import from environment"
          description="Imports AI Hub secrets from configured server environment variables when both required values are present. Secret values are never returned to the browser."
          control={
            <div className="flex flex-col items-end gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => importAIHub.mutate()}
                disabled={importAIHub.isPending}
              >
                Import AI Hub
              </Button>
              {importResult && (
                <span className="text-right text-xs text-muted-foreground">
                  {importResult.imported.length
                    ? `Imported ${importResult.imported.length} secrets from ${importResult.source_prefix}.`
                    : "No complete AI Hub environment shape found."}
                </span>
              )}
            </div>
          }
        />
        {importShape.data?.candidates.map((candidate) => (
          <SettingsRow
            key={candidate.prefix}
            label={candidate.prefix}
            description={[
              ...candidate.required_secrets.map(
                (secret) =>
                  `${secret.source_env}: ${secret.present ? "present" : "missing"}`
              ),
              `${candidate.model_list_env}: ${
                candidate.model_list_present ? "present" : "missing"
              }`,
            ].join(" · ")}
            control={
              <span className="text-xs text-muted-foreground">
                Import candidate
              </span>
            }
          />
        ))}
        {secretNames.map((name) => (
          <SecretRow
            key={name}
            projectId={projectId}
            environment={environment}
            status={secretStatus(
              secrets.data?.items,
              projectId,
              environment,
              name
            )}
            fixed={AI_HUB_SECRET_NAMES.includes(name)}
          />
        ))}
        <SettingsRow
          label="Custom secret"
          description="Add another project-scoped secret for this environment."
          control={
            <div className="flex w-full flex-col items-stretch gap-2 sm:w-auto sm:items-end">
              <div className="flex flex-col gap-2 sm:flex-row">
                <Input
                  aria-label="Custom secret name"
                  className="w-full sm:w-48"
                  placeholder="SECRET_NAME"
                  value={customName}
                  onChange={(e) => setCustomName(e.target.value)}
                />
                <Input
                  aria-label="Custom secret value"
                  className="w-full sm:w-56"
                  placeholder="Secret value"
                  type="password"
                  value={customValue}
                  onChange={(e) => setCustomValue(e.target.value)}
                />
                <Button
                  size="sm"
                  onClick={() => saveCustom.mutate()}
                  disabled={
                    saveCustom.isPending ||
                    !customName.trim() ||
                    !customValue.trim()
                  }
                >
                  Add
                </Button>
              </div>
              {customError && (
                <p className="text-right text-xs text-destructive">
                  {customError}
                </p>
              )}
            </div>
          }
        />
      </div>
    </SettingsSection>
  )
}
