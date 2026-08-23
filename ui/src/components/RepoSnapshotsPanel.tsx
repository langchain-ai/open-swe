import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import type { RepoSnapshot, RepoSnapshotStatus } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { InstructionsEditor } from "@/components/InstructionsEditor"
import { api, isGithubReauthError, loginUrl } from "@/lib/api"
import { useRepos } from "@/lib/profile"
import { normalizeRepoFullName } from "@/lib/repo"

function formatMutationError(e: Error): string {
  return isGithubReauthError(e)
    ? "GitHub token expired — sign in again using the link above."
    : e.message
}

const STATUS_LABEL: Record<RepoSnapshotStatus, string> = {
  none: "Not built",
  building: "Building…",
  ready: "Ready",
  failed: "Failed",
}

const STATUS_CLASS: Record<RepoSnapshotStatus, string> = {
  none: "text-muted-foreground",
  building: "text-amber-500",
  ready: "text-emerald-500",
  failed: "text-destructive",
}

export function RepoSnapshotsPanel() {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [addRepo, setAddRepo] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [draft, setDraft] = useState("")
  const [baseDraft, setBaseDraft] = useState<string | null>(null)

  const snapshots = useQuery({
    queryKey: ["repoSnapshots"],
    queryFn: api.listRepoSnapshots,
  })

  const sandboxSettings = useQuery({
    queryKey: ["sandboxSettings"],
    queryFn: api.getSandboxSettings,
  })

  const saveBase = useMutation({
    mutationFn: (value: string | null) => api.saveSandboxSettings(value),
    onSuccess: (settings) => {
      qc.setQueryData(["sandboxSettings"], settings)
      setBaseDraft(null)
      setError(null)
    },
    onError: (e: Error) => setError(formatMutationError(e)),
  })

  const repos = useRepos()

  const detail = useQuery({
    queryKey: ["repoSnapshot", selected],
    queryFn: () => api.getRepoSnapshot(selected!),
    enabled: !!selected,
    // Poll while a build is running so status + logs update live.
    refetchInterval: (query) =>
      query.state.data?.status === "building" ? 4000 : false,
  })

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (detail.data) setDraft(detail.data.dockerfile)
  }, [detail.data?.dockerfile, detail.data?.full_name])

  const create = useMutation({
    mutationFn: (full_name: string) => api.createRepoSnapshot(full_name),
    onSuccess: (record) => {
      void qc.invalidateQueries({ queryKey: ["repoSnapshots"] })
      setSelected(record.full_name)
      setError(null)
    },
    onError: (e: Error) => setError(formatMutationError(e)),
  })

  const save = useMutation({
    mutationFn: ({ full_name, value }: { full_name: string; value: string }) =>
      api.saveRepoSnapshot(full_name, { dockerfile: value }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["repoSnapshots"] })
      void qc.invalidateQueries({ queryKey: ["repoSnapshot", selected] })
      setError(null)
    },
    onError: (e: Error) => setError(formatMutationError(e)),
  })

  const build = useMutation({
    mutationFn: (full_name: string) => api.buildRepoSnapshot(full_name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["repoSnapshots"] })
      void qc.invalidateQueries({ queryKey: ["repoSnapshot", selected] })
      setError(null)
    },
    onError: (e: Error) => setError(formatMutationError(e)),
  })

  const remove = useMutation({
    mutationFn: (full_name: string) => api.deleteRepoSnapshot(full_name),
    onSuccess: (_data, full_name) => {
      void qc.invalidateQueries({ queryKey: ["repoSnapshots"] })
      if (selected === full_name) {
        setSelected(null)
        setDraft("")
      }
      setError(null)
    },
    onError: (e: Error) => setError(formatMutationError(e)),
  })

  if (snapshots.isLoading) {
    return <Skeleton className="h-40" />
  }

  const configured = new Set((snapshots.data ?? []).map((s) => s.full_name))
  const suggestedRepos = (repos.data?.repositories ?? []).filter(
    (r) => !configured.has(r.full_name)
  )
  const normalizedAddRepo = normalizeRepoFullName(addRepo)
  const canAdd =
    normalizedAddRepo !== null && !configured.has(normalizedAddRepo)
  const active: RepoSnapshot | null =
    detail.data ?? snapshots.data?.find((s) => s.full_name === selected) ?? null
  const dirty = active != null && draft !== active.dockerfile
  const building = active?.status === "building"

  const handleAdd = () => {
    if (!normalizedAddRepo || !canAdd) return
    void create
      .mutateAsync(normalizedAddRepo)
      .then(() => setAddRepo(""))
      .catch(() => undefined)
  }

  const base = sandboxSettings.data
  const baseValue = baseDraft ?? base?.base_snapshot_id ?? ""
  const baseDirty = baseValue.trim() !== (base?.base_snapshot_id ?? "")
  const baseHint =
    base?.base_snapshot_source === "admin"
      ? `Overrides DEFAULT_SANDBOX_SNAPSHOT_ID${
          base.env_base_snapshot_id ? ` (${base.env_base_snapshot_id})` : ""
        }. New sandboxes boot from this unless the repo has a ready snapshot below.`
      : base?.base_snapshot_source === "env"
        ? `Using DEFAULT_SANDBOX_SNAPSHOT_ID. Set a value here to change it without a redeploy.`
        : "No base snapshot configured — new sandboxes cannot start until one is set here or in DEFAULT_SANDBOX_SNAPSHOT_ID."

  const githubReauth =
    (repos.isError && isGithubReauthError(repos.error)) ||
    (error !== null && /github token|re-login required/i.test(error))

  return (
    <div className="flex flex-col gap-6 p-4">
      {githubReauth && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          Your GitHub connection expired.{" "}
          <a
            href={loginUrl()}
            className="font-medium underline underline-offset-2"
          >
            Sign in with GitHub again
          </a>{" "}
          to list installed repos.
        </div>
      )}
      <section className="space-y-2">
        <Label htmlFor="base-snapshot">Base snapshot</Label>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <Input
            id="base-snapshot"
            placeholder={base?.env_base_snapshot_id ?? "snapshot id"}
            value={baseValue}
            onChange={(e) => setBaseDraft(e.target.value)}
            disabled={sandboxSettings.isLoading}
            className="sm:flex-1"
          />
          <Button
            size="sm"
            className="shrink-0 sm:w-auto"
            disabled={!baseDirty || saveBase.isPending}
            onClick={() => void saveBase.mutateAsync(baseValue.trim() || null)}
          >
            Save
          </Button>
          {base?.base_snapshot_id && (
            <Button
              size="sm"
              variant="secondary"
              className="shrink-0 sm:w-auto"
              disabled={saveBase.isPending}
              onClick={() => void saveBase.mutateAsync(null)}
            >
              Use env default
            </Button>
          )}
        </div>
        <p
          className={`text-xs ${
            base?.base_snapshot_source === "unset"
              ? "text-destructive"
              : "text-muted-foreground"
          }`}
        >
          {baseHint}
        </p>
      </section>

      <div className="border-t border-border" />

      <section className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="add-snapshot-repo">Add repository</Label>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <Input
              id="add-snapshot-repo"
              placeholder="owner/repo"
              value={addRepo}
              onChange={(e) => setAddRepo(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault()
                  handleAdd()
                }
              }}
              className="sm:flex-1"
            />
            <Button
              size="sm"
              className="shrink-0 sm:w-auto"
              disabled={!canAdd || create.isPending}
              onClick={handleAdd}
            >
              Add
            </Button>
          </div>
          {suggestedRepos.length > 0 && (
            <Combobox
              items={suggestedRepos.map((r) => r.full_name)}
              value={addRepo}
              onValueChange={(v) => setAddRepo(typeof v === "string" ? v : "")}
            >
              <ComboboxInput
                placeholder="Search installed repos…"
                showClear
                className="w-full"
              />
              <ComboboxContent className="min-w-[var(--anchor-width)]">
                <ComboboxList className="max-h-48">
                  <ComboboxEmpty>No matches</ComboboxEmpty>
                  {suggestedRepos.map((r) => (
                    <ComboboxItem key={r.full_name} value={r.full_name}>
                      <span className="truncate">{r.full_name}</span>
                      {r.private && (
                        <span className="ml-auto text-[10px] text-muted-foreground">
                          private
                        </span>
                      )}
                    </ComboboxItem>
                  ))}
                </ComboboxList>
              </ComboboxContent>
            </Combobox>
          )}
        </div>

        <div className="space-y-2">
          <p className="text-xs font-medium text-foreground">Repositories</p>
          {(snapshots.data ?? []).length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No repositories yet.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {(snapshots.data ?? []).map((s) => (
                <li key={s.full_name}>
                  <button
                    type="button"
                    className={`inline-flex max-w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-muted ${
                      selected === s.full_name
                        ? "border-primary bg-muted font-medium"
                        : "border-border"
                    }`}
                    onClick={() => setSelected(s.full_name)}
                  >
                    <span className="truncate">{s.full_name}</span>
                    <span className={`text-[10px] ${STATUS_CLASS[s.status]}`}>
                      {STATUS_LABEL[s.status]}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <div className="border-t border-border" />

      <section className="space-y-3">
        {!selected || !active ? (
          <p className="text-xs text-muted-foreground">
            Select a repository above to edit its Dockerfile and build a
            snapshot. Repos without a ready snapshot fall back to the default
            sandbox image.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-medium text-foreground">
                {active.full_name}
              </p>
              <span className={`text-xs ${STATUS_CLASS[active.status]}`}>
                {STATUS_LABEL[active.status]}
              </span>
              {active.snapshot_id && (
                <span className="text-[10px] text-muted-foreground">
                  snapshot {active.snapshot_id}
                </span>
              )}
              {active.last_built_at && (
                <span className="text-[10px] text-muted-foreground">
                  built {new Date(active.last_built_at).toLocaleString()}
                </span>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={!dirty || save.isPending || building}
                onClick={() =>
                  void save.mutateAsync({
                    full_name: active.full_name,
                    value: draft,
                  })
                }
              >
                Save Dockerfile
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={dirty || building || build.isPending}
                onClick={() => void build.mutateAsync(active.full_name)}
              >
                {building ? "Building…" : "Build snapshot"}
              </Button>
              {dirty && (
                <span className="self-center text-xs text-muted-foreground">
                  Save before building
                </span>
              )}
              <Button
                size="sm"
                variant="destructive"
                className="ml-auto"
                disabled={remove.isPending}
                onClick={() => {
                  if (
                    !window.confirm(
                      `Remove the snapshot config for ${active.full_name}? Runs will fall back to the default sandbox image.`
                    )
                  ) {
                    return
                  }
                  void remove.mutateAsync(active.full_name)
                }}
              >
                Remove
              </Button>
            </div>
            <InstructionsEditor
              value={draft}
              onChange={setDraft}
              language="dockerfile"
              disabled={building}
              placeholder="FROM ..."
            />
            {active.status === "failed" && active.status_message && (
              <p className="text-xs text-destructive">
                {active.status_message}
              </p>
            )}
            {active.build_log && (
              <pre className="max-h-64 overflow-auto rounded-md border border-border bg-muted/40 p-3 text-[11px] leading-relaxed whitespace-pre-wrap">
                {active.build_log}
              </pre>
            )}
          </>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </section>
    </div>
  )
}
