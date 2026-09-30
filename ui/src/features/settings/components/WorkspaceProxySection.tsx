import { useState } from "react"
import { useMutation } from "@tanstack/react-query"

import { SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api, type JsonValue, type WorkspaceRecord } from "@/lib/api"

export function WorkspaceProxySection({
  record,
  canEdit,
  onSaved,
}: {
  record: WorkspaceRecord
  canEdit: boolean
  onSaved: (saved: WorkspaceRecord) => void
}) {
  const original = JSON.stringify(
    record.create_params?.proxy_config ?? {},
    null,
    2
  )
  const [draft, setDraft] = useState(original)
  const dirty = draft !== original
  const save = useMutation({
    meta: { errorTitle: "Couldn't save sandbox proxy configuration" },
    mutationFn: () => {
      const parsed: JsonValue = JSON.parse(draft)
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("Proxy configuration must be a JSON object.")
      }
      if ("rules" in parsed && !Array.isArray(parsed.rules)) {
        throw new Error("Proxy rules must be a JSON array.")
      }
      return api.updateWorkspace(record.slug, {
        create_params: { ...record.create_params, proxy_config: parsed },
      })
    },
    onSuccess: onSaved,
  })

  return (
    <SettingsSection
      title="Sandbox proxy"
      description="Configure host-matched headers and sandbox environment variables for new LangSmith sandboxes. Existing sandboxes are unchanged."
    >
      <div className="space-y-3 px-4 py-3.5">
        <label htmlFor="workspace-proxy-config" className="text-sm">
          Proxy configuration (JSON)
        </label>
        <Textarea
          id="workspace-proxy-config"
          className="min-h-64 font-mono text-xs"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={!canEdit || save.isPending}
          spellCheck={false}
          aria-describedby="workspace-proxy-help"
        />
        <p id="workspace-proxy-help" className="text-xs text-muted-foreground">
          Use rules with name, match_hosts, headers (name, type, value), and
          env_vars. Headers match hosts; environment variables are sandbox-wide.
          Do not enter secrets or authentication credentials. Authorization,
          API-key headers, and token-like environment names are rejected. Save
          {" {} "}to clear custom proxy settings; other sandbox create
          parameters are preserved.
        </p>
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">Example configuration</summary>
          <pre className="mt-2 overflow-auto rounded-md bg-muted p-3">
            {JSON.stringify(
              {
                rules: [
                  {
                    name: "custom-service",
                    match_hosts: ["api.example.com"],
                    headers: [
                      {
                        name: "X-Custom-Header",
                        type: "plaintext",
                        value: "example",
                      },
                    ],
                    env_vars: { CUSTOM_SERVICE_MODE: "example" },
                  },
                ],
              },
              null,
              2
            )}
          </pre>
        </details>
        {save.error && (
          <p role="alert" className="text-xs text-destructive">
            {save.error.message}
          </p>
        )}
        {canEdit && (
          <div className="flex justify-end gap-2">
            {dirty && (
              <Button
                size="sm"
                variant="ghost"
                disabled={save.isPending}
                onClick={() => setDraft(original)}
              >
                Cancel
              </Button>
            )}
            <Button
              size="sm"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? "Saving…" : "Save proxy configuration"}
            </Button>
          </div>
        )}
      </div>
    </SettingsSection>
  )
}
