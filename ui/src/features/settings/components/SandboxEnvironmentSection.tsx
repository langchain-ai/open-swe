import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"

export function SandboxEnvironmentSection({
  workspace,
}: {
  workspace?: string
}) {
  const queryClient = useQueryClient()
  const queryKey = ["sandbox-environment", workspace ?? "personal"]
  const variables = useQuery({
    queryKey,
    queryFn: () => api.sandboxEnvironment(workspace),
  })
  const [name, setName] = useState("")
  const [value, setValue] = useState("")
  const save = useMutation({
    mutationFn: (patch: Record<string, string | null>) =>
      api.saveSandboxEnvironment(patch, workspace),
    onSuccess: (result) => {
      queryClient.setQueryData(queryKey, result)
      setName("")
      setValue("")
    },
  })
  return (
    <SettingsSection
      title="Sandbox environment variables"
      description={
        workspace
          ? "Available to every thread in this workspace. Values are encrypted and cannot be read back."
          : "Only injected into your private-thread sandboxes. Personal values override workspace values. Values are encrypted and cannot be read back."
      }
    >
      <div className="space-y-3 p-4">
        {variables.data?.names.map((key) => (
          <div key={key} className="flex items-center justify-between gap-3">
            <code className="text-sm">{key}</code>
            <Button
              variant="outline"
              size="sm"
              disabled={save.isPending}
              onClick={() => save.mutate({ [key]: null })}
            >
              Remove
            </Button>
          </div>
        ))}
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate({ [name]: value })
          }}
        >
          <Input
            aria-label="Variable name"
            placeholder="LANGSMITH_API_KEY"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            pattern="[A-Za-z_][A-Za-z0-9_]*"
            className="min-w-48 flex-1"
          />
          <Input
            aria-label="Variable value"
            type="password"
            autoComplete="new-password"
            placeholder="Value (write-only)"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            className="min-w-48 flex-1"
          />
          <Button type="submit" disabled={save.isPending || !name}>
            Save variable
          </Button>
        </form>
        {(save.error || variables.error) && (
          <p role="alert" className="text-sm text-destructive">
            {(save.error || variables.error)?.message}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          Changes apply when a sandbox connects for its next run. Code running
          in the sandbox can read these values; do not share personal sandboxes
          containing secrets.
        </p>
      </div>
    </SettingsSection>
  )
}
