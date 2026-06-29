import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type {
  WorkspaceModelCapabilities,
  WorkspaceModelRoutingPayload,
  WorkspaceModelRoutingSelection,
  WorkspaceModelRoutingUpdateBody,
} from "@/lib/api"
import { api } from "@/lib/api"

const EFFORTS = ["low", "medium", "high", "xhigh", "max"]
const CAPABILITIES: Array<keyof WorkspaceModelCapabilities> = [
  "tool_calling",
  "vision",
  "reasoning",
  "json_schema_mode",
  "streaming",
]

function label(value: string): string {
  return value.replaceAll("_", " ")
}

function emptySelection(): WorkspaceModelRoutingSelection {
  return { endpoint_id: "", model_id: "", effort: "medium", capabilities: {} }
}

function draftFromPayload(
  data: WorkspaceModelRoutingPayload | undefined
): WorkspaceModelRoutingUpdateBody {
  return {
    environment: data?.environment ?? "default",
    default: data?.routing.default ?? null,
    roles: data?.routing.roles ?? {},
    fallback: data?.routing.fallback ?? null,
  }
}

function selectedEndpoint(
  data: WorkspaceModelRoutingPayload | undefined,
  endpointId: string | undefined
) {
  return (data?.endpoints ?? []).find((endpoint) => endpoint.id === endpointId)
}

function firstModelForEndpoint(
  data: WorkspaceModelRoutingPayload | undefined,
  endpointId: string
): string {
  return selectedEndpoint(data, endpointId)?.models[0]?.model_id ?? ""
}

function RoleRoutingRow({
  role,
  data,
  value,
  onChange,
}: {
  role: string
  data: WorkspaceModelRoutingPayload | undefined
  value: WorkspaceModelRoutingSelection | undefined
  onChange: (next: WorkspaceModelRoutingSelection) => void
}) {
  const selection = value ?? emptySelection()
  const endpoint = selectedEndpoint(data, selection.endpoint_id)
  const models = endpoint?.models ?? []
  const capabilities = selection.capabilities ?? {}

  return (
    <div className="grid gap-3 border-b border-border px-4 py-3 text-xs last:border-b-0 lg:grid-cols-[150px_1fr]">
      <div className="min-w-0">
        <div className="font-medium text-foreground">{label(role)}</div>
        {endpoint ? (
          <div className="mt-1 flex flex-wrap gap-1">
            <Badge variant={endpoint.disabled ? "destructive" : "secondary"}>
              {endpoint.provider_type}
            </Badge>
            <Badge variant="secondary">{endpoint.base_url_fingerprint}</Badge>
          </div>
        ) : null}
      </div>
      <div className="grid gap-2 sm:grid-cols-[1fr_1fr_110px]">
        <select
          aria-label={`${role} endpoint`}
          className="h-7 rounded-md border border-input bg-input/20 px-2"
          value={selection.endpoint_id ?? ""}
          onChange={(e) => {
            const endpointId = e.target.value
            onChange({
              ...selection,
              endpoint_id: endpointId,
              model_id: firstModelForEndpoint(data, endpointId),
            })
          }}
        >
          <option value="">Select endpoint</option>
          {(data?.endpoints ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.display_name} / {item.provider_type}
            </option>
          ))}
        </select>
        <select
          aria-label={`${role} model`}
          className="h-7 rounded-md border border-input bg-input/20 px-2"
          value={selection.model_id}
          onChange={(e) => onChange({ ...selection, model_id: e.target.value })}
        >
          <option value="">Select model</option>
          {models.map((item) => (
            <option key={item.model_id} value={item.model_id}>
              {item.model_id}
            </option>
          ))}
        </select>
        <select
          aria-label={`${role} effort`}
          className="h-7 rounded-md border border-input bg-input/20 px-2"
          value={selection.effort}
          onChange={(e) => onChange({ ...selection, effort: e.target.value })}
        >
          {EFFORTS.map((effort) => (
            <option key={effort} value={effort}>
              {effort}
            </option>
          ))}
        </select>
        <div className="flex flex-wrap gap-3 sm:col-span-3">
          {CAPABILITIES.map((capability) => (
            <label key={capability} className="flex items-center gap-1">
              <input
                aria-label={`${role} ${capability}`}
                type="checkbox"
                checked={Boolean(capabilities[capability])}
                onChange={(e) =>
                  onChange({
                    ...selection,
                    capabilities: {
                      ...capabilities,
                      [capability]: e.target.checked,
                    },
                  })
                }
              />
              <span className="text-muted-foreground">{label(capability)}</span>
            </label>
          ))}
          <label className="flex items-center gap-1">
            <span className="text-muted-foreground">context</span>
            <Input
              aria-label={`${role} context window`}
              className="w-24"
              type="number"
              value={capabilities.context_window ?? ""}
              onChange={(e) =>
                onChange({
                  ...selection,
                  capabilities: {
                    ...capabilities,
                    context_window: Number(e.target.value) || undefined,
                  },
                })
              }
            />
          </label>
        </div>
      </div>
    </div>
  )
}

export function WorkspaceModelRoutingSection({
  projectId,
}: {
  projectId: string
}) {
  const qc = useQueryClient()
  const routing = useQuery({
    queryKey: ["workspaceModelRouting", projectId],
    queryFn: () => api.getWorkspaceModelRouting(projectId),
  })
  const [draft, setDraft] = useState<WorkspaceModelRoutingUpdateBody>(() =>
    draftFromPayload(undefined)
  )
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (routing.data) setDraft(draftFromPayload(routing.data))
  }, [routing.data])

  const roles = useMemo(
    () =>
      (routing.data?.roles ?? []).filter((role) =>
        [
          "orchestrator",
          "executor",
          "reviewer",
          "qa",
          "drupal_backend",
          "drupal_frontend",
          "content",
          "content_editor",
          "vision",
          "browser_proof",
          "helper",
          "subagent",
          "fallback",
        ].includes(role)
      ),
    [routing.data?.roles]
  )

  const save = useMutation({
    mutationFn: () => api.saveWorkspaceModelRouting(projectId, draft),
    onSuccess: (next) => {
      setError(null)
      setDraft(draftFromPayload(next))
      void qc.setQueryData(["workspaceModelRouting", projectId], next)
      void qc.invalidateQueries({ queryKey: ["deliveryProjectReadiness", projectId] })
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <SettingsSection
      title="Model Routing"
      description="Assign endpoint-backed models to delivery agent roles."
    >
      <div className="divide-y divide-border">
        <SettingsRow
          label="Environment"
          description="Routing reads endpoint definitions from this workspace environment."
          control={
            <Input
              aria-label="Model routing environment"
              className="w-full sm:w-48"
              value={draft.environment}
              onChange={(e) =>
                setDraft((current) => ({
                  ...current,
                  environment: e.target.value.trim().toLowerCase() || "default",
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Validation"
          description={
            routing.data?.validation.ready
              ? "Configured routing is ready."
              : (routing.data?.validation.blockers ?? [])
                  .map((blocker) => blocker.message ?? blocker.code)
                  .join(" ")
          }
          control={
            <Badge variant={routing.data?.validation.ready ? "default" : "destructive"}>
              {routing.data?.validation.ready ? "Ready" : "Blocked"}
            </Badge>
          }
        />
      </div>
      <div className="divide-y divide-border">
        {roles.map((role) => (
          <RoleRoutingRow
            key={role}
            role={role}
            data={routing.data}
            value={
              role === "fallback"
                ? (draft.fallback ?? undefined)
                : draft.roles[role]
            }
            onChange={(next) =>
              setDraft((current) =>
                role === "fallback"
                  ? { ...current, fallback: next }
                  : { ...current, roles: { ...current.roles, [role]: next } }
              )
            }
          />
        ))}
      </div>
      <SettingsRow
        label="Actions"
        description={`${routing.data?.endpoints.length ?? 0} endpoints available for role routing.`}
        control={
          <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
            Save routing
          </Button>
        }
      />
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}
