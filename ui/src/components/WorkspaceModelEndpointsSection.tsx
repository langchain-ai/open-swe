import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type {
  WorkspaceModelEndpoint,
  WorkspaceModelEndpointUpdateBody,
  WorkspaceModelEndpointValidation,
} from "@/lib/api"
import { api } from "@/lib/api"

const DEFAULT_ENVIRONMENT = "default"

function splitList(value: string): Array<string> {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
}

function joinList(value: Array<string> | undefined): string {
  return (value ?? []).join(", ")
}

function endpointToBody(
  endpoint: WorkspaceModelEndpoint
): WorkspaceModelEndpointUpdateBody {
  return {
    id: endpoint.id,
    display_name: endpoint.display_name,
    provider_type: endpoint.provider_type,
    base_url: endpoint.base_url,
    api_path: endpoint.api_path,
    auth_type:
      endpoint.auth_type === "api_key" || endpoint.auth_type === "none"
        ? endpoint.auth_type
        : "bearer",
    secret_name: endpoint.secret_name,
    default_headers: Object.fromEntries(
      (endpoint.default_headers ?? []).map((name) => [name, "configured"])
    ),
    model_ids: endpoint.model_ids ?? [],
    organization: endpoint.organization ?? "",
    project: endpoint.project ?? "",
    timeout_seconds: endpoint.timeout_seconds ?? 60,
    rate_limit: endpoint.rate_limit ?? {},
    supports_model_discovery: endpoint.supports_model_discovery,
    disabled: endpoint.disabled,
  }
}

function EndpointEditor({
  projectId,
  environment,
  endpoint,
  validation,
  onValidation,
}: {
  projectId: string
  environment: string
  endpoint: WorkspaceModelEndpoint
  validation?: WorkspaceModelEndpointValidation
  onValidation: (result: WorkspaceModelEndpointValidation) => void
}) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState(() => endpointToBody(endpoint))
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setDraft(endpointToBody(endpoint))
  }, [endpoint])
  const invalidate = () => {
    void qc.invalidateQueries({
      queryKey: ["modelEndpoints", projectId, environment],
    })
    void qc.invalidateQueries({
      queryKey: ["deliveryProjectReadiness", projectId],
    })
  }
  const save = useMutation({
    mutationFn: (body: WorkspaceModelEndpointUpdateBody) =>
      api.saveModelEndpoint(projectId, endpoint.id, environment, body),
    onSuccess: (next) => {
      setError(null)
      setDraft(endpointToBody(next))
      invalidate()
    },
    onError: (e: Error) => setError(e.message),
  })
  const validate = useMutation({
    mutationFn: () => api.validateModelEndpoint(projectId, endpoint.id, environment),
    onSuccess: (result) => {
      setError(null)
      onValidation(result)
    },
    onError: (e: Error) => setError(e.message),
  })
  const remove = useMutation({
    mutationFn: () => api.deleteModelEndpoint(projectId, endpoint.id, environment),
    onSuccess: () => {
      setError(null)
      invalidate()
    },
    onError: (e: Error) => setError(e.message),
  })
  const blockers = validation?.blockers ?? []

  return (
    <div className="divide-y divide-border rounded-md border border-border">
      <div className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-xs font-medium">
              {endpoint.display_name}
            </h3>
            <Badge variant={endpoint.disabled ? "secondary" : "default"}>
              {endpoint.disabled ? "Disabled" : "Enabled"}
            </Badge>
            <Badge
              variant={endpoint.secret.connected ? "default" : "destructive"}
            >
              {endpoint.secret.connected ? "Secret ready" : "Secret missing"}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {endpoint.provider_type} · {endpoint.base_url}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => validate.mutate()}
            disabled={validate.isPending}
          >
            Validate
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              save.mutate({
                ...draft,
                disabled: !draft.disabled,
              })
            }
            disabled={save.isPending}
          >
            {draft.disabled ? "Enable" : "Disable"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => remove.mutate()}
            disabled={remove.isPending}
          >
            Remove
          </Button>
        </div>
      </div>
      <div className="grid gap-3 p-4 text-xs sm:grid-cols-2">
        <label className="space-y-1">
          <span className="font-medium text-muted-foreground">Display name</span>
          <Input
            aria-label={`${endpoint.id} display name`}
            value={draft.display_name}
            onChange={(e) =>
              setDraft((current) => ({
                ...current,
                display_name: e.target.value,
              }))
            }
          />
        </label>
        <label className="space-y-1">
          <span className="font-medium text-muted-foreground">Base URL</span>
          <Input
            aria-label={`${endpoint.id} base URL`}
            value={draft.base_url}
            onChange={(e) =>
              setDraft((current) => ({ ...current, base_url: e.target.value }))
            }
          />
        </label>
        <label className="space-y-1">
          <span className="font-medium text-muted-foreground">API path</span>
          <Input
            aria-label={`${endpoint.id} API path`}
            value={draft.api_path}
            onChange={(e) =>
              setDraft((current) => ({ ...current, api_path: e.target.value }))
            }
          />
        </label>
        <label className="space-y-1">
          <span className="font-medium text-muted-foreground">Secret ref</span>
          <Input
            aria-label={`${endpoint.id} secret reference`}
            value={draft.secret_name}
            onChange={(e) =>
              setDraft((current) => ({
                ...current,
                secret_name: e.target.value,
              }))
            }
          />
        </label>
        <label className="space-y-1">
          <span className="font-medium text-muted-foreground">Model IDs</span>
          <Input
            aria-label={`${endpoint.id} model IDs`}
            value={joinList(draft.model_ids)}
            onChange={(e) =>
              setDraft((current) => ({
                ...current,
                model_ids: splitList(e.target.value),
              }))
            }
          />
        </label>
        <label className="space-y-1">
          <span className="font-medium text-muted-foreground">Header names</span>
          <Input
            aria-label={`${endpoint.id} header names`}
            value={joinList(Object.keys(draft.default_headers))}
            onChange={(e) =>
              setDraft((current) => ({
                ...current,
                default_headers: Object.fromEntries(
                  splitList(e.target.value).map((name) => [name, "configured"])
                ),
              }))
            }
          />
        </label>
      </div>
      <SettingsRow
        label="Options"
        description={[
          `timeout ${draft.timeout_seconds}s`,
          draft.supports_model_discovery ? "model discovery" : "manual models",
          draft.organization ? `org ${draft.organization}` : "",
          draft.project ? `project ${draft.project}` : "",
        ]
          .filter(Boolean)
          .join(" · ")}
        control={
          <Button
            size="sm"
            onClick={() => save.mutate(draft)}
            disabled={save.isPending}
          >
            Save endpoint
          </Button>
        }
      />
      {validation && (
        <p
          className={`px-4 pb-3 text-xs ${
            validation.ready ? "text-primary" : "text-destructive"
          }`}
        >
          {validation.ready
            ? "Endpoint validation passed."
            : blockers.map((blocker) => blocker.message ?? blocker.code).join(" ")}
        </p>
      )}
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </div>
  )
}

export function WorkspaceModelEndpointsSection({
  projectId,
}: {
  projectId: string
}) {
  const qc = useQueryClient()
  const [environment, setEnvironment] = useState(DEFAULT_ENVIRONMENT)
  const [preset, setPreset] = useState("ai_hub")
  const [validationById, setValidationById] = useState<
    Record<string, WorkspaceModelEndpointValidation>
  >({})
  const [error, setError] = useState<string | null>(null)

  const endpoints = useQuery({
    queryKey: ["modelEndpoints", projectId, environment],
    queryFn: () => api.listModelEndpoints(projectId, environment),
  })
  const presets = useQuery({
    queryKey: ["modelEndpointPresets", projectId],
    queryFn: () => api.listModelEndpointPresets(projectId),
  })
  const createPreset = useMutation({
    mutationFn: () => api.createModelEndpointPreset(projectId, preset, environment),
    onSuccess: () => {
      setError(null)
      void qc.invalidateQueries({
        queryKey: ["modelEndpoints", projectId, environment],
      })
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <SettingsSection
      title="Model Endpoints"
      description="Manage workspace model endpoint definitions separately from per-agent routing."
    >
      <div id="models" className="divide-y divide-border">
        <SettingsRow
          label="Environment"
          description="Endpoint definitions are scoped by workspace environment."
          control={
            <Input
              aria-label="Model endpoint environment"
              className="w-full sm:w-48"
              value={environment}
              onChange={(e) =>
                setEnvironment(e.target.value.trim().toLowerCase() || DEFAULT_ENVIRONMENT)
              }
            />
          }
        />
        <SettingsRow
          label="Preset"
          description="Create a built-in endpoint definition, then adjust it for this workspace."
          control={
            <div className="flex flex-wrap justify-end gap-2">
              <select
                aria-label="Model endpoint preset"
                className="h-7 w-full rounded-md border border-input bg-input/20 px-2 text-xs sm:w-56"
                value={preset}
                onChange={(e) => setPreset(e.target.value)}
              >
                {(presets.data?.items ?? []).map((item) => (
                  <option key={item.provider_type} value={item.provider_type}>
                    {item.display_name}
                  </option>
                ))}
              </select>
              <Button
                size="sm"
                onClick={() => createPreset.mutate()}
                disabled={createPreset.isPending}
              >
                Add preset
              </Button>
            </div>
          }
        />
      </div>
      <div className="space-y-3 p-4">
        {endpoints.data?.items.length ? (
          endpoints.data.items.map((endpoint) => (
            <EndpointEditor
              key={endpoint.id}
              projectId={projectId}
              environment={environment}
              endpoint={endpoint}
              validation={validationById[endpoint.id]}
              onValidation={(result) =>
                setValidationById((current) => ({
                  ...current,
                  [endpoint.id]: result,
                }))
              }
            />
          ))
        ) : (
          <p className="text-xs text-muted-foreground">
            No model endpoints configured.
          </p>
        )}
      </div>
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}
