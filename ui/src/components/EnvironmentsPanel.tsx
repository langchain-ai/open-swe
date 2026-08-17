import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import type { Environment, EnvironmentSnapshotStatus } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { InstructionsEditor } from "@/components/InstructionsEditor"
import { api } from "@/lib/api"
import { normalizeRepoFullName } from "@/lib/repo"

const STATUS_LABEL: Record<EnvironmentSnapshotStatus, string> = {
  none: "No snapshot",
  capturing: "Capturing…",
  ready: "Ready",
  failed: "Failed",
}

const STATUS_CLASS: Record<EnvironmentSnapshotStatus, string> = {
  none: "text-muted-foreground",
  capturing: "text-amber-500",
  ready: "text-emerald-500",
  failed: "text-destructive",
}

function parseRepos(value: string): Array<string> {
  const entries = value
    .split(/[\s,]+/)
    .map((entry) => entry.trim())
    .filter(Boolean)
  return entries.map((entry) => normalizeRepoFullName(entry) ?? entry)
}

export function EnvironmentsPanel() {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [promptDraft, setPromptDraft] = useState("")
  const [reposDraft, setReposDraft] = useState("")

  const list = useQuery({
    queryKey: ["environments"],
    queryFn: api.listEnvironments,
    // Poll while a capture is running so status updates without a reload.
    refetchInterval: (query) =>
      query.state.data?.environments.some(
        (env) => env.snapshot_status === "capturing"
      )
        ? 5000
        : false,
  })

  const environments = list.data?.environments ?? []
  const defaultSlug = list.data?.default_slug ?? "default"
  const active: Environment | null =
    environments.find((env) => env.slug === selected) ?? null

  useEffect(() => {
    if (!active) return
    setPromptDraft(active.prompt)
    setReposDraft(active.repos.join("\n"))
  }, [active?.slug, active?.updated_at])

  const invalidate = () => qc.invalidateQueries({ queryKey: ["environments"] })
  const onError = (e: Error) => setError(e.message)

  const save = useMutation({
    mutationFn: ({
      slug,
      prompt,
      repos,
    }: {
      slug: string
      prompt: string
      repos: Array<string>
    }) => api.saveEnvironment(slug, { prompt, repos }),
    onSuccess: () => {
      void invalidate()
      setError(null)
    },
    onError,
  })

  const remove = useMutation({
    mutationFn: (slug: string) => api.deleteEnvironment(slug),
    onSuccess: (_data, slug) => {
      void invalidate()
      if (selected === slug) setSelected(null)
      setError(null)
    },
    onError,
  })

  if (list.isLoading) return <Skeleton className="h-40" />

  const dirty =
    active != null &&
    (promptDraft !== active.prompt ||
      parseRepos(reposDraft).join("\n") !== active.repos.join("\n"))

  return (
    <div className="flex flex-col gap-6 p-4">
      <section className="space-y-2">
        <p className="text-xs text-muted-foreground">
          Create environments from an admin thread: enable Admin in the
          new-agent composer, provision its sandbox, then ask the agent to save
          and capture it. Runs without an explicit selection use the environment
          named <code>default</code>.
        </p>
      </section>

      <div className="border-t border-border" />

      <section className="space-y-2">
        <p className="text-xs font-medium text-foreground">Environments</p>
        {environments.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No environments yet. Runs boot from the per-repo or base snapshot.
          </p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {environments.map((env) => (
              <li key={env.slug}>
                <button
                  type="button"
                  className={`inline-flex max-w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-muted ${
                    selected === env.slug
                      ? "border-primary bg-muted font-medium"
                      : "border-border"
                  }`}
                  onClick={() => setSelected(env.slug)}
                >
                  <span className="truncate">{env.name}</span>
                  {env.slug === defaultSlug && (
                    <span className="text-[10px] text-primary">default</span>
                  )}
                  <span
                    className={`text-[10px] ${STATUS_CLASS[env.snapshot_status]}`}
                  >
                    {STATUS_LABEL[env.snapshot_status]}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="border-t border-border" />

      <section className="space-y-3">
        {!active ? (
          <p className="text-xs text-muted-foreground">
            Select an environment to edit its prompt and repositories.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-medium text-foreground">
                {active.name}
              </p>
              {active.slug === defaultSlug ? (
                <span className="text-[10px] text-primary">
                  runs boot from this
                </span>
              ) : (
                <span className="text-[10px] text-muted-foreground">
                  draft — no run boots from this
                </span>
              )}
              <span
                className={`text-xs ${STATUS_CLASS[active.snapshot_status]}`}
              >
                {STATUS_LABEL[active.snapshot_status]}
              </span>
              {active.snapshot_name && (
                <span className="text-[10px] text-muted-foreground">
                  {active.snapshot_name}
                </span>
              )}
              {active.snapshot_id && (
                <span className="text-[10px] text-muted-foreground">
                  snapshot {active.snapshot_id}
                </span>
              )}
              {active.last_captured_at && (
                <span className="text-[10px] text-muted-foreground">
                  captured {new Date(active.last_captured_at).toLocaleString()}
                </span>
              )}
            </div>

            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={!dirty || save.isPending}
                onClick={() =>
                  void save.mutateAsync({
                    slug: active.slug,
                    prompt: promptDraft,
                    repos: parseRepos(reposDraft),
                  })
                }
              >
                Save
              </Button>
              <Button
                size="sm"
                variant="destructive"
                className="ml-auto"
                disabled={remove.isPending}
                onClick={() => {
                  if (
                    !window.confirm(
                      `Delete ${active.name} and its snapshot? Runs fall back to the per-repo or base snapshot.`
                    )
                  ) {
                    return
                  }
                  void remove.mutateAsync(active.slug)
                }}
              >
                Delete
              </Button>
            </div>

            <div className="space-y-2">
              <Label htmlFor="environment-repos">Repositories</Label>
              <Input
                id="environment-repos"
                placeholder="owner/repo, owner/other-repo"
                value={reposDraft}
                onChange={(e) => setReposDraft(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                What this environment&rsquo;s snapshot contains. Documentation
                only — listing a repo does not clone it.
              </p>
            </div>

            <div className="space-y-2">
              <Label>Environment prompt</Label>
              <InstructionsEditor
                value={promptDraft}
                onChange={setPromptDraft}
                placeholder="Where checkouts live, how to build and test, what is pre-installed…"
              />
            </div>

            {/* Shown even when Ready: a failed recapture keeps the previous
                snapshot bootable, so the error is the only sign it happened. */}
            {active.status_message && (
              <p className="text-xs text-destructive">
                {active.snapshot_status === "ready"
                  ? `Last capture failed, still booting from the previous snapshot: ${active.status_message}`
                  : active.status_message}
              </p>
            )}
          </>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </section>
    </div>
  )
}
