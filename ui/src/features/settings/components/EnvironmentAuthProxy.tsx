import { useId, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  api,
  type EnvironmentAuthProxyRule,
  type EnvironmentAuthProxyRuleUpdate,
  type EnvironmentAuthProxyRules,
  type EnvironmentAuthProxyScheme,
} from "@/lib/api"

const MAX_RULES = 20
const RULE_ID_RE = /^[a-z][a-z0-9-]{0,31}$/
const HEADER_RE = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/

type DraftRule = EnvironmentAuthProxyRule & {
  key: string
  credential: string
}

function exactDnsHostname(host: string): boolean {
  if (!host || host.length > 253 || host.includes(":")) return false
  if (/^\d+(?:\.\d+){3}$/.test(host)) return false
  return host
    .split(".")
    .every(
      (label) =>
        label.length > 0 &&
        label.length <= 63 &&
        /^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$/.test(label)
    )
}

function toDraft(rule: EnvironmentAuthProxyRule): DraftRule {
  return {
    ...rule,
    key: `saved:${rule.id}`,
    credential: "",
  }
}

function newRuleId(): string {
  return `rule-${globalThis.crypto.randomUUID().replaceAll("-", "").slice(0, 27)}`
}

function buildUpdate(rules: DraftRule[]): EnvironmentAuthProxyRuleUpdate[] {
  const seen = new Set<string>()
  return rules.map((rule) => {
    const id = rule.id.trim()
    const host = rule.host.trim()
    const header = rule.header.trim()
    if (!RULE_ID_RE.test(id)) {
      throw new Error(
        "Rule IDs must use lowercase letters and numbers separated by hyphens."
      )
    }
    if (seen.has(id)) throw new Error("Rule IDs must be unique.")
    seen.add(id)
    if (!exactDnsHostname(host)) {
      throw new Error(
        "Enter an exact DNS hostname without a URL, port, wildcard, or IP address."
      )
    }
    if (!HEADER_RE.test(header)) {
      throw new Error("Enter a valid HTTP header name.")
    }
    if (!rule.has_credential && !rule.credential.trim()) {
      throw new Error(`Enter a credential for ${id}.`)
    }
    return {
      id,
      host,
      header,
      scheme: rule.scheme,
      ...(rule.credential ? { credential: rule.credential } : {}),
    }
  })
}

export function EnvironmentAuthProxy({
  environmentSlug,
  environmentName,
}: {
  environmentSlug: string
  environmentName: string
}) {
  const queryClient = useQueryClient()
  const panelId = useId()
  const [open, setOpen] = useState(false)
  const [rules, setRules] = useState<DraftRule[] | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const queryKey = ["environment-auth-proxy", environmentSlug] as const
  const authProxy = useQuery({
    queryKey,
    queryFn: () => api.getEnvironmentAuthProxy(environmentSlug),
    enabled: open,
  })
  const currentRules = rules ?? authProxy.data?.rules.map(toDraft) ?? []

  const close = () => {
    setOpen(false)
    setRules(null)
    setValidationError(null)
    setSaveError(null)
    setSaved(false)
  }

  const updateRule = (key: string, update: Partial<DraftRule>) => {
    setRules(
      currentRules.map((rule) =>
        rule.key === key ? { ...rule, ...update } : rule
      )
    )
    setValidationError(null)
    setSaveError(null)
    setSaved(false)
  }

  const changeScheme = (
    rule: DraftRule,
    scheme: EnvironmentAuthProxyScheme
  ) => {
    const previousDefault =
      rule.scheme === "bearer" ? "Authorization" : "X-API-Key"
    const nextDefault = scheme === "bearer" ? "Authorization" : "X-API-Key"
    updateRule(rule.key, {
      scheme,
      header: rule.header === previousDefault ? nextDefault : rule.header,
    })
  }

  const submit = async () => {
    setValidationError(null)
    setSaveError(null)
    setSaved(false)
    let body: { rules: EnvironmentAuthProxyRuleUpdate[] }
    try {
      body = { rules: buildUpdate(currentRules) }
    } catch (error) {
      setValidationError(
        error instanceof Error ? error.message : "Unable to validate rules."
      )
      return
    }
    setSaving(true)
    try {
      const result = await api.saveEnvironmentAuthProxy(environmentSlug, body)
      queryClient.setQueryData<EnvironmentAuthProxyRules>(queryKey, result)
      setRules(result.rules.map(toDraft))
      setSaved(true)
      setSaving(false)
    } catch (error) {
      setSaveError(
        error instanceof Error ? error.message : "Unable to save rules."
      )
      setSaving(false)
    }
  }

  return (
    <div className="border-t border-border/70 pt-2">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-controls={panelId}
        aria-expanded={open}
        aria-label={
          open
            ? `Close authentication proxy for ${environmentName}`
            : `Configure authentication proxy for ${environmentName}`
        }
        onClick={() => (open ? close() : setOpen(true))}
      >
        {open ? "Close authentication proxy" : "Authentication proxy"}
      </Button>
      {open && (
        <div id={panelId} className="mt-3 space-y-3">
          {authProxy.isLoading ? (
            <p className="text-xs text-muted-foreground">
              Loading authentication rules…
            </p>
          ) : authProxy.isError ? (
            <div className="space-y-2">
              <p role="alert" className="text-xs text-destructive">
                {authProxy.error.message ||
                  "Could not load authentication rules."}
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void authProxy.refetch()}
              >
                Retry
              </Button>
            </div>
          ) : (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault()
                void submit()
              }}
            >
              <p className="text-xs/relaxed text-muted-foreground">
                Add up to 20 rules for exact DNS hostnames. Credentials stay
                masked after saving and apply on the next proxy refresh.
              </p>
              {(validationError || saveError) && (
                <p role="alert" className="text-xs text-destructive">
                  {validationError ?? saveError}
                </p>
              )}
              {saved && (
                <p role="status" className="text-xs text-muted-foreground">
                  Authentication rules saved.
                </p>
              )}
              {currentRules.length === 0 ? (
                <p className="rounded-md border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
                  No authentication rules configured.
                </p>
              ) : (
                <div className="space-y-3">
                  {currentRules.map((rule, index) => {
                    const label = `rule ${index + 1}`
                    return (
                      <fieldset
                        key={rule.key}
                        disabled={saving}
                        className="space-y-3 rounded-md border border-border p-3"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-xs font-medium text-foreground">
                            {rule.host || `Rule ${index + 1}`}
                          </span>
                          <Button
                            type="button"
                            size="xs"
                            variant="destructive"
                            aria-label={`Remove ${label}`}
                            onClick={() => {
                              setRules(
                                currentRules.filter(
                                  (item) => item.key !== rule.key
                                )
                              )
                              setValidationError(null)
                              setSaveError(null)
                              setSaved(false)
                            }}
                          >
                            Remove
                          </Button>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                          <label className="block text-xs">
                            Host
                            <Input
                              aria-label={`Host for ${label}`}
                              required
                              value={rule.host}
                              placeholder="api.example.com"
                              autoCapitalize="none"
                              onChange={(event) =>
                                updateRule(rule.key, {
                                  host: event.target.value,
                                })
                              }
                            />
                          </label>
                          <label className="block text-xs">
                            Authentication method
                            <select
                              aria-label={`Authentication method for ${label}`}
                              className="mt-1 block h-7 w-full rounded-md border border-input bg-background px-2 text-xs"
                              value={rule.scheme}
                              onChange={(event) =>
                                changeScheme(
                                  rule,
                                  event.target
                                    .value as EnvironmentAuthProxyScheme
                                )
                              }
                            >
                              <option value="bearer">Bearer token</option>
                              <option value="api_key">API key</option>
                            </select>
                          </label>
                          <label className="block text-xs">
                            Header
                            <Input
                              aria-label={`Header for ${label}`}
                              required
                              value={rule.header}
                              onChange={(event) =>
                                updateRule(rule.key, {
                                  header: event.target.value,
                                })
                              }
                            />
                          </label>
                        </div>
                        <label className="block text-xs">
                          Credential
                          <Input
                            aria-label={`Credential for ${label}`}
                            required={!rule.has_credential}
                            type="password"
                            autoComplete="new-password"
                            value={rule.credential}
                            placeholder={
                              rule.has_credential
                                ? "Leave blank to keep current credential"
                                : "Enter credential"
                            }
                            onChange={(event) =>
                              updateRule(rule.key, {
                                credential: event.target.value,
                              })
                            }
                          />
                          <span className="text-[11px] text-muted-foreground">
                            {rule.has_credential ? (
                              <>
                                <span aria-hidden="true">•••••••• · </span>
                                <span>Credential configured</span>
                              </>
                            ) : (
                              "Credential required"
                            )}
                          </span>
                        </label>
                      </fieldset>
                    )
                  })}
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={saving || currentRules.length >= MAX_RULES}
                  onClick={() => {
                    const id = newRuleId()
                    setRules([
                      ...currentRules,
                      {
                        key: `new:${id}`,
                        id,
                        host: "",
                        header: "Authorization",
                        scheme: "bearer",
                        has_credential: false,
                        credential: "",
                      },
                    ])
                    setValidationError(null)
                    setSaveError(null)
                    setSaved(false)
                  }}
                >
                  Add rule
                </Button>
                <Button type="submit" size="sm" disabled={saving}>
                  {saving ? "Saving…" : "Save rules"}
                </Button>
              </div>
            </form>
          )}
        </div>
      )}
    </div>
  )
}
