import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import type { DesktopProviderKey } from "@/desktop"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SettingsRow, SettingsSection } from "@/components/AppShell"

const PROVIDER_LABELS: Record<string, string> = {
  ANTHROPIC_API_KEY: "Anthropic",
  OPENAI_API_KEY: "OpenAI",
  GOOGLE_API_KEY: "Google Gemini",
  FIREWORKS_API_KEY: "Fireworks",
}

export function ApiKeysSection() {
  const qc = useQueryClient()
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  const keys = useQuery({
    queryKey: ["desktopProviderKeys"],
    queryFn: () => window.openSweDesktop?.listProviderKeys() ?? [],
    enabled: Boolean(window.openSweDesktop),
  })

  const mutate = useMutation({
    mutationFn: async (input: { variable: string; value?: string }) =>
      input.value === undefined
        ? await window.openSweDesktop?.clearProviderKey(input.variable)
        : await window.openSweDesktop?.setProviderKey({
            variable: input.variable,
            value: input.value,
          }),
    onSuccess: (data, input) => {
      if (data) qc.setQueryData(["desktopProviderKeys"], data)
      setDrafts((current) => ({ ...current, [input.variable]: "" }))
      setError(null)
    },
    onError: (cause: Error) => setError(cause.message),
  })

  if (!window.openSweDesktop || !keys.data?.length) return null

  return (
    <SettingsSection
      title="Model API keys"
      description="Keys for the local LangGraph server that runs your desktop agents. They are encrypted on this device, passed only to the local server, and never uploaded. Saving a key restarts the local server."
    >
      {keys.data.map((key: DesktopProviderKey) => (
        <SettingsRow
          key={key.variable}
          label={PROVIDER_LABELS[key.variable] ?? key.variable}
          description={
            key.configured ? "Key saved on this device" : key.variable
          }
          control={
            <div className="flex items-center gap-2">
              <Input
                type="password"
                autoComplete="off"
                className="w-56"
                placeholder={key.configured ? "Replace key" : "Paste API key"}
                value={drafts[key.variable] ?? ""}
                onChange={(event) =>
                  setDrafts((current) => ({
                    ...current,
                    [key.variable]: event.target.value,
                  }))
                }
              />
              <Button
                size="sm"
                disabled={
                  !(drafts[key.variable] ?? "").trim() || mutate.isPending
                }
                onClick={() =>
                  void mutate.mutateAsync({
                    variable: key.variable,
                    value: drafts[key.variable] ?? "",
                  })
                }
              >
                Save
              </Button>
              {key.configured && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={mutate.isPending}
                  onClick={() =>
                    void mutate.mutateAsync({ variable: key.variable })
                  }
                >
                  Remove
                </Button>
              )}
            </div>
          }
        />
      ))}
      {error && <p className="px-4 py-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}
