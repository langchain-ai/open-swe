import { InfoRegularIcon } from "@langchain/macaw-components/icons"
import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { Tooltip } from "@langchain/macaw-components/Tooltip"

import { SettingsSection } from "@/components/AppShell"
import { api, type JsonValue, type WorkspaceRecord } from "@/lib/api"

function FieldHelp({
  label,
  description,
}: {
  label: string
  description: string
}) {
  return (
    <Tooltip title={description} tooltipClassName="max-w-80">
      {/* A span, not a button: it sits inside the field's <label>. */}
      <span
        role="button"
        tabIndex={0}
        aria-label={`About ${label.toLowerCase()}`}
        className="ml-space-1 inline-flex align-middle text-icon-secondary hover:text-icon-primary"
        onClick={(event) => event.preventDefault()}
      >
        <InfoRegularIcon aria-hidden="true" size={14} />
      </span>
    </Tooltip>
  )
}

function FieldLabel({
  label,
  description,
}: {
  label: string
  description: string
}) {
  return (
    <span className="text-xs">
      {label}
      <FieldHelp label={label} description={description} />
    </span>
  )
}

const HEADER_TYPES = [
  { value: "plaintext", label: "Plaintext" },
  { value: "opaque", label: "Opaque" },
]

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
  const [view, setView] = useState<"form" | "json">("form")
  const dirty = draft !== original
  let config: Record<string, JsonValue> | null = null
  let parseError: string | null = null
  try {
    const parsed: JsonValue = JSON.parse(draft)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Proxy configuration must be a JSON object.")
    }
    if ("rules" in parsed && !Array.isArray(parsed.rules)) {
      throw new Error("Proxy rules must be a JSON array.")
    }
    config = parsed
  } catch (error) {
    parseError = error instanceof Error ? error.message : "Invalid JSON"
  }
  const rules = config && Array.isArray(config.rules) ? config.rules : []
  const changeRules = (next: JsonValue[]) =>
    setDraft(JSON.stringify({ ...config, rules: next }, null, 2))
  const changeRule = (index: number, next: Record<string, JsonValue>) =>
    changeRules(
      rules.map((rule, position) => (position === index ? next : rule))
    )
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
      <div className="space-y-3 px-space-4 py-3.5 text-primary">
        <div
          role="tablist"
          aria-label="Proxy editor view"
          className="flex gap-space-2"
        >
          {(["form", "json"] as const).map((tab) => (
            <Button
              key={tab}
              role="tab"
              aria-selected={view === tab}
              aria-controls={`proxy-${tab}`}
              id={`proxy-tab-${tab}`}
              color="secondary"
              variant={view === tab ? "outlined" : "plain"}
              size="xs"
              onClick={() => setView(tab)}
            >
              {tab === "form" ? "Rules" : "JSON"}
            </Button>
          ))}
        </div>
        <div
          role="tabpanel"
          id={`proxy-${view}`}
          aria-labelledby={`proxy-tab-${view}`}
        >
          {view === "json" ? (
            <>
              <label htmlFor="workspace-proxy-config" className="text-sm">
                Proxy configuration (JSON)
                <FieldHelp
                  label="Proxy configuration (JSON)"
                  description="Advanced view of the same proxy configuration. Switching tabs preserves edits, including fields not exposed in the Rules form. Other sandbox create parameters are preserved on save."
                />
              </label>
              <Textarea
                id="workspace-proxy-config"
                aria-label="Proxy configuration (JSON)"
                size="md"
                className="mt-space-1"
                inputClassName="min-h-64 font-mono text-xs"
                value={draft}
                onChange={setDraft}
                disabled={!canEdit || save.isPending}
                spellCheck={false}
                aria-describedby="workspace-proxy-help"
              />
            </>
          ) : parseError ? (
            <p role="alert" className="text-xs text-error-secondary">
              {parseError} Fix the configuration in the JSON tab to use the
              form.
            </p>
          ) : (
            <fieldset
              disabled={!canEdit || save.isPending}
              className="space-y-4"
            >
              {rules.length === 0 && (
                <p className="text-sm text-secondary">
                  No custom proxy rules configured.
                </p>
              )}
              {rules.map((ruleValue, index) => {
                if (
                  !ruleValue ||
                  typeof ruleValue !== "object" ||
                  Array.isArray(ruleValue)
                )
                  return (
                    <p key={index} role="alert">
                      Rule {index + 1} is not an object. Edit it in the JSON
                      tab.
                    </p>
                  )
                const rule = ruleValue
                const headers = Array.isArray(rule.headers) ? rule.headers : []
                const env =
                  rule.env_vars &&
                  typeof rule.env_vars === "object" &&
                  !Array.isArray(rule.env_vars)
                    ? rule.env_vars
                    : {}
                return (
                  <div
                    key={index}
                    className="space-y-3 rounded-md border border-default p-space-3"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">
                        Rule {index + 1}
                      </span>
                      <Button
                        size="xs"
                        color="secondary"
                        variant="plain"
                        onClick={() =>
                          changeRules(
                            rules.filter((_, position) => position !== index)
                          )
                        }
                      >
                        {`Remove rule ${index + 1}`}
                      </Button>
                    </div>
                    <Input
                      size="sm"
                      label={
                        <FieldLabel
                          label="Rule name"
                          description="Required name identifying this proxy rule. Use a descriptive name such as custom-service; avoid Open SWE’s built-in rule names such as github and github-api."
                        />
                      }
                      value={typeof rule.name === "string" ? rule.name : ""}
                      placeholder="custom-service"
                      onChange={(name) => changeRule(index, { ...rule, name })}
                    />
                    <Input
                      size="sm"
                      label={
                        <FieldLabel
                          label="Matching hosts"
                          description="Comma-separated bare hostnames, without a scheme, port, or path. Use api.example.com for an exact host or *.example.com for subdomains. The wildcard does not match example.com itself. Bare * and public-suffix wildcards such as *.com are rejected."
                        />
                      }
                      value={
                        Array.isArray(rule.match_hosts)
                          ? rule.match_hosts.join(", ")
                          : ""
                      }
                      placeholder="api.example.com, *.example.com"
                      onChange={(hosts) =>
                        changeRule(index, {
                          ...rule,
                          match_hosts: hosts
                            .split(",")
                            .map((host) => host.trim()),
                        })
                      }
                    />
                    <div className="text-xs font-medium">Headers</div>
                    {headers.map((header, position) => {
                      if (
                        !header ||
                        typeof header !== "object" ||
                        Array.isArray(header)
                      )
                        return (
                          <p key={position} role="alert">
                            Invalid header. Edit it in the JSON tab.
                          </p>
                        )
                      const update = (next: Record<string, JsonValue>) =>
                        changeRule(index, {
                          ...rule,
                          headers: headers.map((item, i) =>
                            i === position ? next : item
                          ),
                        })
                      return (
                        <div
                          key={position}
                          className="flex flex-wrap items-end gap-space-2"
                        >
                          <Input
                            size="sm"
                            className="min-w-32 flex-1"
                            label={
                              <FieldLabel
                                label="Header name"
                                description="HTTP header injected into outbound requests matching this rule’s hosts, for example X-Custom-Header. Open SWE workspace settings reject authentication headers such as Authorization and X-Api-Key."
                              />
                            }
                            value={
                              typeof header.name === "string" ? header.name : ""
                            }
                            placeholder="X-Custom-Header"
                            onChange={(name) => update({ ...header, name })}
                          />
                          <Input
                            size="sm"
                            className="min-w-32 flex-1"
                            label={
                              <FieldLabel
                                label="Header value"
                                description="Value the proxy injects for this header on matching outbound requests. Only non-secret values may be saved in Open SWE workspace settings."
                              />
                            }
                            value={
                              typeof header.value === "string"
                                ? header.value
                                : ""
                            }
                            onChange={(value) => update({ ...header, value })}
                          />
                          <div className="space-y-1">
                            <FieldLabel
                              label="Type"
                              description="Plaintext values are stored and returned as-is by the sandbox API. Opaque values are encrypted and write-only there, but Open SWE still persists this workspace configuration: opaque is not a way to store secrets here."
                            />
                            <Select
                              aria-label="Type"
                              options={HEADER_TYPES}
                              value={
                                typeof header.type === "string"
                                  ? header.type
                                  : "plaintext"
                              }
                              onChange={(type) =>
                                type && update({ ...header, type })
                              }
                              disabled={!canEdit || save.isPending}
                              triggerClassName="w-32"
                            />
                          </div>
                          <Button
                            size="sm"
                            color="secondary"
                            variant="plain"
                            aria-label={`Remove header ${position + 1} from rule ${index + 1}`}
                            onClick={() =>
                              changeRule(index, {
                                ...rule,
                                headers: headers.filter(
                                  (_, i) => i !== position
                                ),
                              })
                            }
                          >
                            Remove
                          </Button>
                        </div>
                      )
                    })}
                    <Button
                      size="xs"
                      color="secondary"
                      variant="outlined"
                      onClick={() =>
                        changeRule(index, {
                          ...rule,
                          headers: [
                            ...headers,
                            { name: "", type: "plaintext", value: "" },
                          ],
                        })
                      }
                    >
                      Add header
                    </Button>
                    <div className="text-xs font-medium">
                      Environment variables
                    </div>
                    {Object.entries(env).map(([name, value], position) => (
                      <div
                        key={position}
                        className="flex items-end gap-space-2"
                      >
                        <Input
                          size="sm"
                          className="flex-1"
                          label={
                            <FieldLabel
                              label="Variable name"
                              description="Name of an environment variable set for every sandbox command while this rule is enabled, not just requests to matching hosts. Token-like names are rejected by Open SWE workspace settings."
                            />
                          }
                          value={name}
                          onChange={(renamed) =>
                            changeRule(index, {
                              ...rule,
                              env_vars: Object.fromEntries(
                                Object.entries(env).map(([key, val]) =>
                                  key === name ? [renamed, val] : [key, val]
                                )
                              ),
                            })
                          }
                        />
                        <Input
                          size="sm"
                          className="flex-1"
                          label={
                            <FieldLabel
                              label="Variable value"
                              description="Plaintext value available inside the sandbox. A dummy value can satisfy tools that require an environment variable while the proxy injects a header on the wire. Explicit sandbox environment variables override rule variables; provider-managed AWS/GCP variables take precedence. Never enter credentials."
                            />
                          }
                          value={typeof value === "string" ? value : ""}
                          onChange={(next) =>
                            changeRule(index, {
                              ...rule,
                              env_vars: { ...env, [name]: next },
                            })
                          }
                        />
                        <Button
                          size="sm"
                          color="secondary"
                          variant="plain"
                          aria-label={`Remove variable ${position + 1} from rule ${index + 1}`}
                          onClick={() =>
                            changeRule(index, {
                              ...rule,
                              env_vars: Object.fromEntries(
                                Object.entries(env).filter(
                                  ([key]) => key !== name
                                )
                              ),
                            })
                          }
                        >
                          Remove
                        </Button>
                      </div>
                    ))}
                    <Button
                      size="xs"
                      color="secondary"
                      variant="outlined"
                      onClick={() => {
                        let name = "NEW_VARIABLE"
                        while (name in env) name += "_"
                        changeRule(index, {
                          ...rule,
                          env_vars: { ...env, [name]: "" },
                        })
                      }}
                    >
                      Add environment variable
                    </Button>
                  </div>
                )
              })}
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                onClick={() =>
                  changeRules([
                    ...rules,
                    { name: "", match_hosts: [], headers: [], env_vars: {} },
                  ])
                }
              >
                Add rule
              </Button>
            </fieldset>
          )}
        </div>
        <p id="workspace-proxy-help" className="text-xs text-secondary">
          Use rules with name, match_hosts, headers (name, type, value), and
          env_vars. Headers match hosts; environment variables are sandbox-wide.
          Do not enter secrets or authentication credentials. Authorization,
          API-key headers, and token-like environment names are rejected. Save
          {" {} "}to clear custom proxy settings; other sandbox create
          parameters are preserved.
        </p>
        <details className="text-xs text-secondary">
          <summary className="cursor-pointer">Example configuration</summary>
          <pre className="mt-space-2 overflow-auto rounded-md bg-surface-level-2 p-space-3">
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
          <p role="alert" className="text-xs text-error-secondary">
            {save.error.message}
          </p>
        )}
        {canEdit && (
          <div className="flex justify-end gap-space-2">
            {dirty && (
              <Button
                size="xs"
                color="secondary"
                variant="plain"
                disabled={save.isPending}
                onClick={() => setDraft(original)}
              >
                Cancel
              </Button>
            )}
            <Button
              size="xs"
              color="primary"
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
