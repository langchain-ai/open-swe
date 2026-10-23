import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"

import { SettingsSection } from "@/components/AppShell"
import { api, type SandboxSecret } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { ConfirmDialog } from "./ConfirmDialog"

const QUERY_KEY = ["sandbox-secrets"]
const BLANK = {
  name: "",
  value: "",
  hosts: "",
  headerName: "Authorization",
  headerFormat: "Bearer {value}",
}

type Draft = typeof BLANK

function hostList(hosts: string): string[] {
  return hosts
    .split(/[\s,]+/)
    .map((host) => host.trim())
    .filter(Boolean)
}

export function SandboxSecretsSection() {
  const qc = useQueryClient()
  const secrets = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.listSandboxSecrets,
  })
  const [draft, setDraft] = useState<Draft>(BLANK)
  const [editing, setEditing] = useState<string | null>(null)
  const [removing, setRemoving] = useState<SandboxSecret | null>(null)
  const change = (field: keyof Draft) => (value: string) =>
    setDraft((current) => ({ ...current, [field]: value }))
  const reset = () => {
    setDraft(BLANK)
    setEditing(null)
  }

  const save = useMutation({
    meta: { errorTitle: "Couldn't save sandbox secret" },
    mutationFn: (next: Draft) =>
      api.saveSandboxSecret(next.name.trim(), {
        hosts: hostList(next.hosts),
        header_name: next.headerName.trim(),
        header_format: next.headerFormat,
        value: next.value ? next.value : null,
      }),
    onSuccess: () => {
      reset()
      void qc.invalidateQueries({ queryKey: QUERY_KEY })
    },
  })

  const remove = useMutation({
    meta: { errorTitle: "Couldn't delete sandbox secret" },
    mutationFn: (name: string) => api.deleteSandboxSecret(name),
    onMutate: async (name) => ({
      undo: await optimisticUpdate<SandboxSecret[]>(qc, QUERY_KEY, (current) =>
        current.filter((secret) => secret.name !== name)
      ),
    }),
    onError: (_error, _name, context) => context?.undo(),
    onSettled: () => setRemoving(null),
  })

  const edit = (secret: SandboxSecret) => {
    setEditing(secret.name)
    setDraft({
      name: secret.name,
      value: "",
      hosts: secret.hosts.join(", "),
      headerName: secret.header_name,
      headerFormat: secret.header_format,
    })
  }

  const ready =
    draft.name.trim() &&
    hostList(draft.hosts).length > 0 &&
    draft.headerName.trim() &&
    draft.headerFormat.includes("{value}") &&
    (editing !== null || draft.value)

  return (
    <SettingsSection
      title="Sandbox Secrets"
      description="API keys for your private threads. The sandbox only sees a placeholder in the environment variable; its auth proxy adds the real value as a header on requests to the hosts you list. Shared threads never get them."
    >
      <div className="space-y-space-4 px-space-4 py-space-3">
        {secrets.isPending ? (
          <p className="text-sm text-secondary">Loading sandbox secrets…</p>
        ) : secrets.isError ? (
          <p role="alert" className="text-sm text-error-secondary">
            {secrets.error.message}
          </p>
        ) : secrets.data.length === 0 ? (
          <p className="text-sm text-secondary">No sandbox secrets yet.</p>
        ) : (
          <ul className="divide-y divide-default">
            {secrets.data.map((secret) => (
              <li
                key={secret.name}
                className="flex flex-wrap items-center justify-between gap-space-3 py-space-3"
              >
                <div className="min-w-0 space-y-space-1">
                  <p className="font-mono text-sm text-primary">
                    {secret.name}
                  </p>
                  <p className="text-xs break-words text-secondary">
                    <span className="font-mono">{secret.header_name}</span> on{" "}
                    {secret.hosts.join(", ")}
                  </p>
                </div>
                <div className="flex gap-space-2">
                  <Button
                    size="xs"
                    color="secondary"
                    variant="outlined"
                    onClick={() => edit(secret)}
                  >
                    Edit<span className="sr-only"> {secret.name}</span>
                  </Button>
                  <Button
                    size="xs"
                    color="error"
                    onClick={() => setRemoving(secret)}
                  >
                    Delete<span className="sr-only"> {secret.name}</span>
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        <form
          className="grid gap-space-3 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (ready && !save.isPending) save.mutate(draft)
          }}
        >
          <Input
            label="Environment variable"
            size="md"
            value={draft.name}
            maxLength={64}
            placeholder="LANGSMITH_API_KEY"
            disabled={editing !== null}
            onChange={(value) => change("name")(value.toUpperCase())}
          />
          <Input
            label={editing ? "New value (blank keeps it)" : "Value"}
            size="md"
            type="password"
            autoComplete="off"
            value={draft.value}
            onChange={change("value")}
          />
          <Input
            label="Hosts"
            size="md"
            value={draft.hosts}
            placeholder="api.smith.langchain-dev.com, *.example.com"
            onChange={change("hosts")}
          />
          <div className="grid grid-cols-2 gap-space-3">
            <Input
              label="Header"
              size="md"
              value={draft.headerName}
              placeholder="x-api-key"
              onChange={change("headerName")}
            />
            <Input
              label="Header value"
              size="md"
              value={draft.headerFormat}
              placeholder="Bearer {value}"
              onChange={change("headerFormat")}
            />
          </div>
          <div className="flex gap-space-2 sm:col-span-2">
            <Button
              type="submit"
              size="xs"
              color="primary"
              disabled={!ready || save.isPending}
            >
              {save.isPending
                ? "Saving…"
                : editing
                  ? `Update ${editing}`
                  : "Add secret"}
            </Button>
            {editing && (
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                onClick={reset}
              >
                Cancel
              </Button>
            )}
          </div>
        </form>
      </div>
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(open) => {
          if (!open) setRemoving(null)
        }}
        title={`Delete ${removing?.name}?`}
        description="Your private threads stop receiving it from their next message."
        confirmLabel="Delete secret"
        destructive
        onConfirm={() => {
          if (removing) remove.mutate(removing.name)
        }}
      />
    </SettingsSection>
  )
}
