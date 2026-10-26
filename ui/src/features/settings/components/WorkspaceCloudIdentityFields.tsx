import { Input } from "@langchain/macaw-components/Input"
import { Button } from "@langchain/macaw-components/Button"

import type { JsonValue } from "@/lib/api"

export const CLOUDS = ["aws", "gcp", "azure"] as const
export type Cloud = (typeof CLOUDS)[number]

export function cloudType(rule: Record<string, JsonValue>): Cloud | undefined {
  const cloud = CLOUDS.find((candidate) => rule.type === candidate)
  if (!cloud) return undefined
  const settings = rule[cloud]
  const staticField = {
    aws: "access_key_id",
    gcp: "service_account_json",
    azure: "storage_account",
  }[cloud]
  if (
    settings &&
    typeof settings === "object" &&
    !Array.isArray(settings) &&
    staticField in settings
  )
    return undefined
  return cloud
}

const FIELDS: Record<
  Cloud,
  { key: string; label: string; placeholder: string }[]
> = {
  aws: [
    {
      key: "role_arn",
      label: "AWS Role ARN",
      placeholder: "arn:aws:iam::123456789012:role/SandboxReadOnly",
    },
  ],
  gcp: [
    {
      key: "workload_identity_provider",
      label: "GCP Workload Identity Provider",
      placeholder:
        "//iam.googleapis.com/projects/123456789/locations/global/workloadIdentityPools/pool/providers/provider",
    },
    {
      key: "service_account_email",
      label: "Service Account Email (Optional)",
      placeholder: "sandbox@project.iam.gserviceaccount.com",
    },
  ],
  azure: [
    { key: "tenant_id", label: "Azure Tenant ID", placeholder: "Tenant UUID" },
    {
      key: "client_id",
      label: "Azure Client ID",
      placeholder: "Application or managed identity UUID",
    },
    {
      key: "resources",
      label: "Azure Resources (Comma-Separated)",
      placeholder: "https://storage.azure.com",
    },
  ],
}

export function WorkspaceCloudIdentityFields({
  cloud,
  rule,
  onChange,
  onRemove,
}: {
  cloud: Cloud
  rule: Record<string, JsonValue>
  onChange: (rule: Record<string, JsonValue>) => void
  onRemove: () => void
}) {
  const stored = rule[cloud]
  const identity =
    stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {}
  const change = (key: string, value: JsonValue) => {
    const next = { ...identity }
    if (value === "" || (Array.isArray(value) && value.length === 0))
      delete next[key]
    else next[key] = value
    onChange({ ...rule, [cloud]: next })
  }
  return (
    <div className="space-y-space-3 rounded-md border border-default p-space-3">
      <div className="flex items-center justify-between gap-space-2">
        <span className="text-sm font-medium">
          {cloud === "azure" ? "Azure" : cloud.toUpperCase()} Workload Identity
        </span>
        <Button size="xs" color="secondary" variant="plain" onClick={onRemove}>
          {`Remove ${cloud === "azure" ? "Azure" : cloud.toUpperCase()} identity`}
        </Button>
      </div>
      <Input
        size="sm"
        label="Rule Name"
        value={typeof rule.name === "string" ? rule.name : ""}
        onChange={(name) => onChange({ ...rule, name })}
      />
      <p className="text-xs text-secondary">
        The proxy uses short-lived cloud credentials outside the sandbox. Grant
        only the permissions this workspace needs; cloud trust must pin the
        LangSmith workspace subject.
      </p>
      {FIELDS[cloud].map(({ key, label, placeholder }) => (
        <Input
          key={key}
          label={label}
          size="sm"
          placeholder={placeholder}
          value={
            Array.isArray(identity[key])
              ? identity[key].join(", ")
              : typeof identity[key] === "string"
                ? identity[key]
                : ""
          }
          onChange={(value) =>
            change(
              key,
              key === "resources"
                ? value
                    .split(",")
                    .map((item) => item.trim())
                    .filter(Boolean)
                : value
            )
          }
        />
      ))}
      <Input
        label="Subject Scope"
        size="sm"
        hintText="Use workspace or tag:<LangSmith tag value UUID>. A tag scope must also be assigned below."
        placeholder="workspace"
        value={
          typeof identity.subject_scope === "string"
            ? identity.subject_scope
            : "workspace"
        }
        onChange={(value) => change("subject_scope", value)}
      />
    </div>
  )
}
