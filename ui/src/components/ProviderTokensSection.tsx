import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import type {
  ProviderPATProvider,
  ProviderPATStatus,
  ProviderPATTestResult,
  ProviderPATUpdateBody,
} from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

const PROVIDERS: Array<{
  provider: ProviderPATProvider
  label: string
  blocked: string
}> = [
  {
    provider: "github",
    label: "GitHub",
    blocked:
      "Missing token blocks branch creation, draft PR delivery, PR QA evidence upload, and merge policy checks that require your GitHub permissions.",
  },
  {
    provider: "linear",
    label: "Linear",
    blocked:
      "Missing token blocks personal Linear queue intake, assignment-aware status updates, and tracker operations that require your Linear permissions.",
  },
]

function formatUpdatedAt(value: string | null | undefined): string {
  if (!value) return ""
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

function testResultClassName(result: ProviderPATTestResult | null): string {
  if (!result) return "text-muted-foreground"
  if (result.status === "valid") return "text-primary"
  if (result.status === "invalid" || result.status === "missing") {
    return "text-destructive"
  }
  return "text-muted-foreground"
}

function statusForProvider(
  items: Array<ProviderPATStatus> | undefined,
  provider: ProviderPATProvider
): ProviderPATStatus {
  return (
    items?.find((item) => item.provider === provider) ?? {
      connected: false,
      provider,
    }
  )
}

function ProviderTokenRow({
  provider,
  label,
  blocked,
  status,
  isLoading,
  isPreferred,
}: {
  provider: ProviderPATProvider
  label: string
  blocked: string
  status: ProviderPATStatus
  isLoading: boolean
  isPreferred: boolean
}) {
  const qc = useQueryClient()
  const [token, setToken] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<ProviderPATTestResult | null>(
    null
  )
  const inputId = `provider-token-${provider}`

  useEffect(() => {
    if (!isPreferred || isLoading) return
    const input = document.getElementById(inputId)
    if (!(input instanceof HTMLInputElement)) return
    const scrollIntoView = input.scrollIntoView as
      | ((options?: ScrollIntoViewOptions) => void)
      | undefined
    scrollIntoView?.call(input, { block: "center" })
    input.focus({ preventScroll: true })
  }, [inputId, isLoading, isPreferred])

  const onSuccess = () => {
    setToken("")
    setError(null)
    setTestResult(null)
    void qc.invalidateQueries({ queryKey: ["myProviderTokens"] })
    void qc.invalidateQueries({ queryKey: ["deliveryProjects"] })
    void qc.invalidateQueries({ queryKey: ["deliveryProjectReadiness"] })
    void qc.invalidateQueries({ queryKey: ["ticketIntake"] })
  }
  const onError = (e: Error) => {
    setError(e.message)
    setTestResult(null)
  }

  const save = useMutation({
    mutationFn: (body: ProviderPATUpdateBody) =>
      api.saveMyProviderToken(provider, body),
    onSuccess,
    onError,
  })
  const revoke = useMutation({
    mutationFn: () => api.revokeMyProviderToken(provider),
    onSuccess,
    onError,
  })
  const test = useMutation({
    mutationFn: () => api.testMyProviderToken(provider),
    onSuccess: (result) => {
      setError(null)
      setTestResult(result)
    },
    onError,
  })

  const connected = status.connected
  const last4 = status.token_last4 ? `token ••••${status.token_last4}` : ""
  const updatedAt = formatUpdatedAt(status.updated_at)
  const metadata = connected
    ? [last4, updatedAt ? `updated ${updatedAt}` : ""]
        .filter(Boolean)
        .join(" · ")
    : blocked

  return (
    <SettingsRow
      label={label}
      description={metadata}
      control={
        <div className="flex w-full flex-col items-stretch gap-2 sm:w-auto sm:items-end">
          <div className="flex flex-wrap items-center justify-end gap-2">
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-[10px] font-medium",
                connected
                  ? "bg-primary/10 text-primary"
                  : "bg-muted text-muted-foreground"
              )}
            >
              {connected ? "Connected" : "Not connected"}
            </span>
            {connected && (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={`Test ${label} token`}
                  onClick={() => test.mutate()}
                  disabled={test.isPending}
                >
                  {test.isPending ? "Testing..." : "Test"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={`Revoke ${label} token`}
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
              id={inputId}
              aria-label={`${label} personal access token`}
              className="w-full sm:w-64"
              placeholder={connected ? "New token" : "Personal access token"}
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              disabled={isLoading || save.isPending}
            />
            <Button
              size="sm"
              aria-label={`${connected ? "Update" : "Save"} ${label} token`}
              onClick={() => save.mutate({ token: token.trim() })}
              disabled={isLoading || save.isPending || !token.trim()}
            >
              {connected ? "Update" : "Save"}
            </Button>
          </div>
          {error && (
            <p className="max-w-72 text-right text-xs text-destructive">
              {error}
            </p>
          )}
          {testResult && (
            <p
              className={cn(
                "max-w-72 text-right text-xs",
                testResultClassName(testResult)
              )}
            >
              {testResult.identity
                ? `${testResult.message} ${testResult.identity}`
                : testResult.message}
            </p>
          )}
        </div>
      }
    />
  )
}

export function ProviderTokensSection({
  preferredProvider,
}: {
  preferredProvider?: ProviderPATProvider
}) {
  const tokens = useQuery({
    queryKey: ["myProviderTokens"],
    queryFn: api.listMyProviderTokens,
  })

  return (
    <SettingsSection
      title="Provider Tokens"
      description="Connect user-scoped provider tokens for delivery work that must run with your GitHub or Linear permissions. Tokens are encrypted at rest and never shown after save."
    >
      <div className="divide-y divide-border">
        {PROVIDERS.map((item) => (
          <ProviderTokenRow
            key={item.provider}
            provider={item.provider}
            label={item.label}
            blocked={item.blocked}
            status={statusForProvider(tokens.data?.items, item.provider)}
            isLoading={tokens.isLoading}
            isPreferred={preferredProvider === item.provider}
          />
        ))}
      </div>
      {tokens.error && (
        <p className="px-4 pb-3 text-xs text-destructive">
          {tokens.error.message}
        </p>
      )}
    </SettingsSection>
  )
}
