import { useId, useState, type ReactElement } from "react"
import { useMutation } from "@tanstack/react-query"

import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@langchain/gtm-platform-design-system/ui/collapsible"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { Label } from "@langchain/gtm-platform-design-system/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@langchain/gtm-platform-design-system/ui/select"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@langchain/gtm-platform-design-system/ui/tabs"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@langchain/gtm-platform-design-system/ui/tooltip"
import { Info } from "@/components/glyphs"
import { api, type JsonValue, type WorkspaceRecord } from "@/lib/api"

import { FormError } from "./WorkspaceSandboxSection"

const HEADER_TYPES = [
  { value: "plaintext", label: "Plaintext" },
  { value: "opaque", label: "Opaque" },
]

function FieldHelp({
  label,
  description,
}: {
  label: string
  description: string
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        aria-label={`About ${label.toLowerCase()}`}
        className="inline-flex cursor-help rounded-tick text-ink-subtle hover:text-ink focus-visible:outline-2 focus-visible:outline-primary"
      >
        <Icon icon={Info} size="sm" />
      </TooltipTrigger>
      <TooltipContent>{description}</TooltipContent>
    </Tooltip>
  )
}

/**
 * A labelled proxy field whose explanation sits behind an info tip: the
 * rules form repeats per rule and per header, so permanent help under every
 * field would bury the values.
 */
function ProxyField({
  label,
  help,
  className,
  children,
}: {
  label: string
  help: string
  className?: string
  children: (id: string) => ReactElement
}) {
  const id = useId()
  return (
    <Stack gap="xs" className={className}>
      <Inline gap="xs" align="center">
        <Label htmlFor={id}>{label}</Label>
        <FieldHelp label={label} description={help} />
      </Inline>
      {children(id)}
    </Stack>
  )
}

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
    <PageSection
      contained
      inset="padded"
      title="Sandbox proxy"
      description="Configure host-matched headers and sandbox environment variables for new LangSmith sandboxes. Existing sandboxes are unchanged."
    >
      <Tabs
        value={view}
        onValueChange={(next) => setView(next === "json" ? "json" : "form")}
      >
        <TabsList aria-label="Proxy editor view">
          <TabsTrigger value="form">Rules</TabsTrigger>
          <TabsTrigger value="json">JSON</TabsTrigger>
        </TabsList>
        <TabsContent value="json">
          <ProxyField
            label="Proxy configuration (JSON)"
            help="Advanced view of the same proxy configuration. Switching tabs preserves edits, including fields not exposed in the Rules form. Other sandbox create parameters are preserved on save."
          >
            {(id) => (
              <Textarea
                id={id}
                aria-label="Proxy configuration (JSON)"
                className="min-h-64 font-mono"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                disabled={!canEdit || save.isPending}
                spellCheck={false}
                aria-describedby="workspace-proxy-help"
              />
            )}
          </ProxyField>
        </TabsContent>
        <TabsContent value="form">
          {parseError ? (
            <FormError
              message={`${parseError} Fix the configuration in the JSON tab to use the form.`}
            />
          ) : (
            <Stack
              render={<fieldset disabled={!canEdit || save.isPending} />}
              gap="lg"
              className="m-0 min-w-0 border-0 p-0"
            >
              {rules.length === 0 && (
                <Box render={<p />} className="text-label text-ink-subtle">
                  No custom proxy rules configured.
                </Box>
              )}
              {rules.map((ruleValue, index) => {
                if (
                  !ruleValue ||
                  typeof ruleValue !== "object" ||
                  Array.isArray(ruleValue)
                )
                  return (
                    <FormError
                      key={index}
                      message={`Rule ${index + 1} is not an object. Edit it in the JSON tab.`}
                    />
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
                  <Stack
                    key={index}
                    gap="md"
                    className="border-b border-line pb-4"
                  >
                    <Inline gap="sm" align="center" justify="between">
                      <Box
                        render={<h3 />}
                        className="text-label font-medium text-ink"
                      >
                        Rule {index + 1}
                      </Box>
                      <Button
                        size="compact"
                        variant="ghost"
                        onClick={() =>
                          changeRules(
                            rules.filter((_, position) => position !== index)
                          )
                        }
                      >
                        Remove rule {index + 1}
                      </Button>
                    </Inline>
                    <ProxyField
                      label="Rule name"
                      help="Required name identifying this proxy rule. Use a descriptive name such as custom-service; avoid Open SWE’s built-in rule names such as github and github-api."
                    >
                      {(id) => (
                        <Input
                          id={id}
                          value={typeof rule.name === "string" ? rule.name : ""}
                          placeholder="custom-service"
                          onChange={(event) =>
                            changeRule(index, {
                              ...rule,
                              name: event.target.value,
                            })
                          }
                        />
                      )}
                    </ProxyField>
                    <ProxyField
                      label="Matching hosts"
                      help="Comma-separated bare hostnames, without a scheme, port, or path. Use api.example.com for an exact host or *.example.com for subdomains. The wildcard does not match example.com itself. Bare * and public-suffix wildcards such as *.com are rejected."
                    >
                      {(id) => (
                        <Input
                          id={id}
                          value={
                            Array.isArray(rule.match_hosts)
                              ? rule.match_hosts.join(", ")
                              : ""
                          }
                          placeholder="api.example.com, *.example.com"
                          onChange={(event) =>
                            changeRule(index, {
                              ...rule,
                              match_hosts: event.target.value
                                .split(",")
                                .map((host) => host.trim()),
                            })
                          }
                        />
                      )}
                    </ProxyField>
                    <Box
                      render={<h4 />}
                      className="text-label font-medium text-ink"
                    >
                      Headers
                    </Box>
                    {headers.map((header, position) => {
                      if (
                        !header ||
                        typeof header !== "object" ||
                        Array.isArray(header)
                      )
                        return (
                          <FormError
                            key={position}
                            message="Invalid header. Edit it in the JSON tab."
                          />
                        )
                      const update = (next: Record<string, JsonValue>) =>
                        changeRule(index, {
                          ...rule,
                          headers: headers.map((item, i) =>
                            i === position ? next : item
                          ),
                        })
                      return (
                        <Inline key={position} gap="sm" align="end" wrap>
                          <ProxyField
                            label="Header name"
                            help="HTTP header injected into outbound requests matching this rule’s hosts, for example X-Custom-Header. Open SWE workspace settings reject authentication headers such as Authorization and X-Api-Key."
                            className="min-w-32 flex-1"
                          >
                            {(id) => (
                              <Input
                                id={id}
                                value={
                                  typeof header.name === "string"
                                    ? header.name
                                    : ""
                                }
                                placeholder="X-Custom-Header"
                                onChange={(event) =>
                                  update({
                                    ...header,
                                    name: event.target.value,
                                  })
                                }
                              />
                            )}
                          </ProxyField>
                          <ProxyField
                            label="Header value"
                            help="Value the proxy injects for this header on matching outbound requests. Only non-secret values may be saved in Open SWE workspace settings."
                            className="min-w-32 flex-1"
                          >
                            {(id) => (
                              <Input
                                id={id}
                                value={
                                  typeof header.value === "string"
                                    ? header.value
                                    : ""
                                }
                                onChange={(event) =>
                                  update({
                                    ...header,
                                    value: event.target.value,
                                  })
                                }
                              />
                            )}
                          </ProxyField>
                          <ProxyField
                            label="Type"
                            help="Plaintext values are stored and returned as-is by the sandbox API. Opaque values are encrypted and write-only there, but Open SWE still persists this workspace configuration: opaque is not a way to store secrets here."
                          >
                            {(id) => (
                              <Select
                                items={HEADER_TYPES}
                                value={
                                  typeof header.type === "string"
                                    ? header.type
                                    : "plaintext"
                                }
                                onValueChange={(next) => {
                                  if (next) update({ ...header, type: next })
                                }}
                                disabled={!canEdit || save.isPending}
                              >
                                <SelectTrigger id={id} className="w-32">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {HEADER_TYPES.map((item) => (
                                    <SelectItem
                                      key={item.value}
                                      value={item.value}
                                    >
                                      {item.label}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            )}
                          </ProxyField>
                          <Button
                            variant="ghost"
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
                        </Inline>
                      )
                    })}
                    <Box>
                      <Button
                        size="compact"
                        variant="outline"
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
                    </Box>
                    <Box
                      render={<h4 />}
                      className="text-label font-medium text-ink"
                    >
                      Environment variables
                    </Box>
                    {Object.entries(env).map(([name, value], position) => (
                      <Inline key={position} gap="sm" align="end" wrap>
                        <ProxyField
                          label="Variable name"
                          help="Name of an environment variable set for every sandbox command while this rule is enabled, not just requests to matching hosts. Token-like names are rejected by Open SWE workspace settings."
                          className="min-w-32 flex-1"
                        >
                          {(id) => (
                            <Input
                              id={id}
                              value={name}
                              onChange={(event) =>
                                changeRule(index, {
                                  ...rule,
                                  env_vars: Object.fromEntries(
                                    Object.entries(env).map(([key, val]) =>
                                      key === name
                                        ? [event.target.value, val]
                                        : [key, val]
                                    )
                                  ),
                                })
                              }
                            />
                          )}
                        </ProxyField>
                        <ProxyField
                          label="Variable value"
                          help="Plaintext value available inside the sandbox. A dummy value can satisfy tools that require an environment variable while the proxy injects a header on the wire. Explicit sandbox environment variables override rule variables; provider-managed AWS/GCP variables take precedence. Never enter credentials."
                          className="min-w-32 flex-1"
                        >
                          {(id) => (
                            <Input
                              id={id}
                              value={typeof value === "string" ? value : ""}
                              onChange={(event) =>
                                changeRule(index, {
                                  ...rule,
                                  env_vars: {
                                    ...env,
                                    [name]: event.target.value,
                                  },
                                })
                              }
                            />
                          )}
                        </ProxyField>
                        <Button
                          variant="ghost"
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
                      </Inline>
                    ))}
                    <Box>
                      <Button
                        size="compact"
                        variant="outline"
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
                    </Box>
                  </Stack>
                )
              })}
              <Box>
                <Button
                  size="compact"
                  variant="outline"
                  onClick={() =>
                    changeRules([
                      ...rules,
                      { name: "", match_hosts: [], headers: [], env_vars: {} },
                    ])
                  }
                >
                  Add rule
                </Button>
              </Box>
            </Stack>
          )}
        </TabsContent>
      </Tabs>
      <Box
        render={<p id="workspace-proxy-help" />}
        className="text-meta text-ink-subtle"
      >
        Use rules with name, match_hosts, headers (name, type, value), and
        env_vars. Headers match hosts; environment variables are sandbox-wide.
        Do not enter secrets or authentication credentials. Authorization,
        API-key headers, and token-like environment names are rejected. Save
        {" {} "}to clear custom proxy settings; other sandbox create parameters
        are preserved.
      </Box>
      <Collapsible>
        <CollapsibleTrigger className="text-meta text-ink-subtle">
          <CollapsibleChevron />
          Example configuration
        </CollapsibleTrigger>
        <CollapsibleContent>
          <Box
            render={<pre />}
            padding="md"
            bg="muted"
            radius="compact"
            className="mt-2 overflow-auto font-mono text-meta text-ink-muted"
          >
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
          </Box>
        </CollapsibleContent>
      </Collapsible>
      {save.error && <FormError message={save.error.message} />}
      {canEdit && (
        <Inline gap="sm" justify="end">
          {dirty && (
            <Button
              size="compact"
              variant="ghost"
              disabled={save.isPending}
              onClick={() => setDraft(original)}
            >
              Cancel
            </Button>
          )}
          <Button
            size="compact"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Save proxy configuration"}
          </Button>
        </Inline>
      )}
    </PageSection>
  )
}
