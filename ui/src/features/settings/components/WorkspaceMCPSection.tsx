import { useId, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { EyeIcon, EyeSlashIcon } from "@phosphor-icons/react"

import { SettingsSection } from "@/components/AppShell"
import { Button, IconButton } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import type { WorkspaceMCP, WorkspaceMCPUpdate } from "@/lib/api"
import { WorkspaceMCPImport } from "./WorkspaceMCPImport"
import type { ImportedMCP } from "./WorkspaceMCPImport"

type Header = { name: string; value: string; revealed?: boolean }
type Draft = Omit<WorkspaceMCPUpdate, "headers"> & { existing: boolean }
const queryKey = ["workspaceMCPs"]

export function WorkspaceMCPSection() {
  const qc = useQueryClient()
  const connections = useQuery({ queryKey, queryFn: api.getWorkspaceMCPs })
  const [draft, setDraft] = useState<Draft | null>(null)
  const [headers, setHeaders] = useState<Header[]>([])
  const [replaceHeaders, setReplaceHeaders] = useState(false)
  const [savedHeaders, setSavedHeaders] = useState<Record<
    string,
    string
  > | null>(null)
  const [catalog, setCatalog] = useState<
    { name: string; description: string }[]
  >([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [toolsExpanded, setToolsExpanded] = useState(true)
  const [importing, setImporting] = useState(false)
  const [pendingImports, setPendingImports] = useState<ImportedMCP[]>([])
  const toolsId = useId()
  const editorId = useId()
  const toolDescriptions = new Map(
    catalog.map((tool) => [tool.name, tool.description])
  )
  const selectedTools = new Set(draft?.allowed_tools)
  const toolNames = [
    ...new Set([
      ...toolDescriptions.keys(),
      ...(connections.data?.find(
        (connection) => connection.name === draft?.name
      )?.allowed_tools ?? []),
      ...selectedTools,
    ]),
  ].sort()

  const openEditor = (
    connection: Draft,
    authentication?: Record<string, string> | null
  ) => {
    setDraft(connection)
    setHeaders(
      Object.entries(authentication ?? {}).map(([name, value]) => ({
        name,
        value,
      }))
    )
    setSavedHeaders(null)
    setReplaceHeaders(authentication != null || !connection.existing)
    setCatalog([])
    setToolsExpanded(true)
    setError(null)
  }

  const edit = (connection?: WorkspaceMCP) => {
    setImporting(false)
    setPendingImports([])
    openEditor(
      connection
        ? { ...connection, existing: true }
        : {
            name: "",
            url: "",
            transport: "streamable_http",
            enabled: true,
            allowed_tools: [],
            existing: false,
          }
    )
  }

  const openImported = ({
    headers: authentication,
    ...connection
  }: ImportedMCP) => {
    const previous = connections.data?.find(
      (saved) => saved.name === connection.name
    )
    openEditor(
      {
        ...connection,
        enabled: previous?.enabled ?? true,
        allowed_tools: previous?.allowed_tools ?? [],
        existing: Boolean(previous),
      },
      authentication
    )
  }

  const closeEditor = () => {
    setDraft(null)
    setHeaders([])
    setSavedHeaders(null)
    setPendingImports([])
    setError(null)
  }

  const finishEditing = () => {
    const [next, ...rest] = pendingImports
    if (next) {
      setPendingImports(rest)
      openImported(next)
    } else closeEditor()
  }

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Unable to update MCP connection"
      )
    }
    setBusy(false)
  }

  const save = async (discover: boolean) => {
    if (!draft) return
    await run(async () => {
      const authentication: Record<string, string> = {}
      if (replaceHeaders) {
        for (const header of headers) {
          const name = header.name.trim()
          if (!name || !header.value.trim())
            throw new Error("Each header needs a name and value")
          if (
            Object.keys(authentication).some(
              (key) => key.toLowerCase() === name.toLowerCase()
            )
          )
            throw new Error("Header names must be unique")
          authentication[name] = header.value
        }
      }
      if (
        !draft.existing &&
        connections.data?.some((c) => c.name === draft.name)
      )
        throw new Error("A connection with this name already exists")
      const update: WorkspaceMCPUpdate = {
        name: draft.name,
        url: draft.url,
        transport: draft.transport,
        enabled: draft.enabled,
        allowed_tools: draft.allowed_tools,
        headers: replaceHeaders ? authentication : null,
      }
      const discovered = discover ? await api.discoverWorkspaceMCP(update) : []
      const saved = await api.saveWorkspaceMCP(update)
      qc.setQueryData<WorkspaceMCP[]>(queryKey, (current) => [
        ...(current ?? []).filter(
          (connection) => connection.name !== saved.name
        ),
        saved,
      ])
      setDraft({
        ...saved,
        existing: true,
        allowed_tools:
          discover && !draft.existing
            ? discovered.map((tool) => tool.name)
            : saved.allowed_tools,
      })
      setHeaders([])
      setSavedHeaders(null)
      setReplaceHeaders(false)
      await qc.invalidateQueries({ queryKey })
      if (discover) {
        setCatalog(discovered)
        setToolsExpanded(true)
      } else finishEditing()
    })
  }

  const updateHeader = (
    index: number,
    field: "name" | "value",
    value: string
  ) =>
    setHeaders(
      headers.map((header, i) =>
        i === index ? { ...header, [field]: value } : header
      )
    )

  const editor = draft ? (
    <form
      id={editorId}
      className={
        draft.existing
          ? "space-y-4 border-t p-4"
          : "space-y-4 rounded-md border p-4"
      }
      onSubmit={(event) => {
        event.preventDefault()
        void save(false)
      }}
    >
      <fieldset disabled={busy} className="space-y-4">
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {pendingImports.length > 0 && (
          <p className="text-xs text-muted-foreground">
            {pendingImports.length} more{" "}
            {pendingImports.length === 1 ? "connection" : "connections"} to
            review after saving.
          </p>
        )}
        <label className="block text-sm">
          Connection name
          <Input
            aria-label="Connection name"
            required
            pattern={"[a-z][a-z0-9_\\-]{0,31}"}
            maxLength={32}
            title="Start with a lowercase letter; use lowercase letters, numbers, hyphens, or underscores."
            placeholder="incident"
            disabled={draft.existing}
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
          <span className="text-xs text-muted-foreground">
            Use a lowercase name such as incident. Dots and spaces are not
            allowed.
          </span>
        </label>
        <label className="block text-sm">
          Server URL
          <Input
            aria-label="Server URL"
            required
            type="url"
            placeholder="https://example.com/mcp"
            value={draft.url}
            onChange={(e) => setDraft({ ...draft, url: e.target.value })}
          />
        </label>
        <label className="block text-sm">
          Transport
          <select
            aria-label="Transport"
            className="mt-1 block w-full rounded-md border bg-background p-2 text-sm"
            value={draft.transport}
            onChange={(e) =>
              setDraft({
                ...draft,
                transport: e.target.value as WorkspaceMCP["transport"],
              })
            }
          >
            <option value="streamable_http">Streamable HTTP</option>
            <option value="sse">SSE</option>
          </select>
        </label>
        <div className="space-y-2">
          <p className="text-sm font-medium">Authentication headers</p>
          <p className="text-xs text-muted-foreground">
            Values are encrypted and hidden by default. Use a header such as
            Authorization or X-API-Key.
          </p>
          {!replaceHeaders ? (
            <>
              {savedHeaders && (
                <div className="space-y-2" data-dd-privacy="hidden">
                  {Object.entries(savedHeaders).map(([name, value]) => (
                    <label key={name} className="block text-sm">
                      {name}
                      <Input
                        aria-label={`Saved ${name} value`}
                        value={value}
                        readOnly
                        autoComplete="off"
                        spellCheck={false}
                      />
                    </label>
                  ))}
                  {Object.keys(savedHeaders).length === 0 && (
                    <p className="text-xs text-muted-foreground">
                      No saved headers.
                    </p>
                  )}
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <IconButton
                  type="button"
                  size="icon-sm"
                  variant="outline"
                  aria-label={
                    savedHeaders ? "Hide saved headers" : "Show saved headers"
                  }
                  title={
                    savedHeaders ? "Hide saved headers" : "Show saved headers"
                  }
                  aria-expanded={savedHeaders !== null}
                  onClick={() => {
                    if (savedHeaders) setSavedHeaders(null)
                    else
                      void run(async () => {
                        setSavedHeaders(
                          await api.revealWorkspaceMCPHeaders(draft.name)
                        )
                      })
                  }}
                >
                  {savedHeaders ? (
                    <EyeSlashIcon aria-hidden="true" />
                  ) : (
                    <EyeIcon aria-hidden="true" />
                  )}
                </IconButton>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setSavedHeaders(null)
                    setReplaceHeaders(true)
                  }}
                >
                  Replace headers
                </Button>
              </div>
            </>
          ) : (
            <>
              {headers.map((header, index) => (
                <div className="flex gap-2" key={index}>
                  <Input
                    aria-label={`Header ${index + 1} name`}
                    placeholder="Authorization"
                    value={header.name}
                    onChange={(e) =>
                      updateHeader(index, "name", e.target.value)
                    }
                  />
                  <Input
                    aria-label={`Header ${index + 1} value`}
                    placeholder="Bearer …"
                    type={header.revealed ? "text" : "password"}
                    autoComplete="off"
                    spellCheck={false}
                    data-dd-privacy="hidden"
                    value={header.value}
                    onChange={(e) =>
                      updateHeader(index, "value", e.target.value)
                    }
                  />
                  <IconButton
                    type="button"
                    size="icon-sm"
                    variant="outline"
                    aria-label={`${header.revealed ? "Hide" : "Show"} header ${index + 1} value`}
                    title={header.revealed ? "Hide value" : "Show value"}
                    aria-pressed={Boolean(header.revealed)}
                    onClick={() =>
                      setHeaders(
                        headers.map((item, i) =>
                          i === index
                            ? { ...item, revealed: !item.revealed }
                            : item
                        )
                      )
                    }
                  >
                    {header.revealed ? (
                      <EyeSlashIcon aria-hidden="true" />
                    ) : (
                      <EyeIcon aria-hidden="true" />
                    )}
                  </IconButton>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    aria-label={`Remove header ${index + 1}`}
                    onClick={() =>
                      setHeaders(headers.filter((_, i) => i !== index))
                    }
                  >
                    Remove
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() =>
                  setHeaders([...headers, { name: "", value: "" }])
                }
              >
                Add header
              </Button>
              {draft.existing && headers.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  Saving with no headers clears the saved authentication.
                </p>
              )}
            </>
          )}
        </div>
        <div className="space-y-2">
          <p className="text-sm font-medium">Allowed tools</p>
          <p className="text-xs text-muted-foreground">
            Discover tools, review the selection, then save. All discovered
            tools are selected by default for new connections.
          </p>
          {toolNames.length > 0 && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className="mr-auto text-xs text-muted-foreground">
                  {draft.allowed_tools.length} of {toolNames.length} selected
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={draft.allowed_tools.length === toolNames.length}
                  onClick={() =>
                    setDraft({ ...draft, allowed_tools: toolNames })
                  }
                >
                  Select all
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={draft.allowed_tools.length === 0}
                  onClick={() => setDraft({ ...draft, allowed_tools: [] })}
                >
                  Clear all
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-expanded={toolsExpanded}
                  aria-controls={toolsId}
                  onClick={() => setToolsExpanded(!toolsExpanded)}
                >
                  {toolsExpanded ? "Hide tools" : "Show tools"}
                </Button>
              </div>
              <div
                id={toolsId}
                role="region"
                aria-label="Available tools"
                tabIndex={0}
                hidden={!toolsExpanded}
                className="max-h-80 space-y-3 overflow-y-auto overscroll-contain rounded-md border p-3"
              >
                {toolNames.map((name) => (
                  <label key={name} className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1 shrink-0"
                      aria-label={`Allow ${name}`}
                      checked={selectedTools.has(name)}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          allowed_tools: e.target.checked
                            ? [...draft.allowed_tools, name]
                            : draft.allowed_tools.filter(
                                (tool) => tool !== name
                              ),
                        })
                      }
                    />
                    <span className="min-w-0 break-words">
                      {name}
                      <span className="block text-xs text-muted-foreground">
                        {toolDescriptions.get(name)}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            </>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!draft.name || !draft.url}
            onClick={(event) => {
              if (event.currentTarget.form?.reportValidity()) void save(true)
            }}
          >
            Save and discover tools
          </Button>
          <Button type="submit" size="sm">
            Save connection
          </Button>
          {pendingImports.length > 0 && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={finishEditing}
            >
              Skip connection
            </Button>
          )}
          <Button type="button" size="sm" variant="ghost" onClick={closeEditor}>
            Cancel
          </Button>
        </div>
      </fieldset>
    </form>
  ) : null

  return (
    <SettingsSection
      title="Workspace MCPs"
      description="Connect remote MCP servers for authorized coding-agent runs. New connections preselect all discovered tools; review the selection and save to enable them."
    >
      <div className="space-y-4 p-4">
        {connections.isLoading && (
          <p className="text-sm text-muted-foreground">Loading connections…</p>
        )}
        {((error && !draft) || connections.error) && (
          <p role="alert" className="text-sm text-destructive">
            {connections.error?.message || error}
          </p>
        )}
        {connections.data?.map((connection) => {
          const isEditing = draft?.existing && draft.name === connection.name
          return (
            <section
              key={connection.name}
              aria-label={`${connection.name} MCP connection`}
              className="rounded-md border"
            >
              <div className="flex flex-wrap items-center justify-between gap-3 p-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium">
                    {connection.name}{" "}
                    <span className="text-muted-foreground">
                      · {connection.enabled ? "Enabled" : "Disabled"} ·{" "}
                      {connection.allowed_tools.length} tools
                    </span>
                  </p>
                  <p className="text-xs break-all text-muted-foreground">
                    {connection.url}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => {
                      if (isEditing) closeEditor()
                      else edit(connection)
                    }}
                    aria-expanded={Boolean(isEditing)}
                    aria-controls={isEditing ? editorId : undefined}
                    aria-label={`${isEditing ? "Close" : "Edit"} ${connection.name}`}
                  >
                    {isEditing ? "Close" : "Edit"}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api.saveWorkspaceMCP({
                          name: connection.name,
                          url: connection.url,
                          transport: connection.transport,
                          enabled: !connection.enabled,
                          allowed_tools: connection.allowed_tools,
                        })
                        await qc.invalidateQueries({ queryKey })
                        if (draft?.name === connection.name) closeEditor()
                      })
                    }
                    aria-label={`${connection.enabled ? "Disable" : "Enable"} ${connection.name}`}
                  >
                    {connection.enabled ? "Disable" : "Enable"}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api.deleteWorkspaceMCP(connection.name)
                        await qc.invalidateQueries({ queryKey })
                        if (draft?.name === connection.name) closeEditor()
                      })
                    }
                    aria-label={`Delete ${connection.name}`}
                  >
                    Delete
                  </Button>
                </div>
              </div>
              {isEditing && editor}
            </section>
          )
        })}
        {importing ? (
          <WorkspaceMCPImport
            onImport={([first, ...rest]) => {
              if (!first) return
              setImporting(false)
              setPendingImports(rest)
              openImported(first)
            }}
            onCancel={() => setImporting(false)}
          />
        ) : !draft ? (
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={connections.isPending || connections.isError}
              onClick={() => edit()}
            >
              Add MCP server
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={connections.isPending || connections.isError}
              onClick={() => {
                setImporting(true)
                setError(null)
              }}
            >
              Import JSON
            </Button>
          </div>
        ) : (
          !draft.existing && editor
        )}
      </div>
    </SettingsSection>
  )
}
