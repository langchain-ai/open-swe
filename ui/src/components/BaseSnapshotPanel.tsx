import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import type { BaseSnapshotSettings, RepoSnapshotStatus } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { InstructionsEditor } from "@/components/InstructionsEditor"
import { api } from "@/lib/api"

const STATUS_LABEL: Record<RepoSnapshotStatus, string> = {
  none: "Never built",
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

const DEFAULT_SETTINGS: BaseSnapshotSettings = {
  enabled: false,
  schedule: "0 9 * * *",
  preclone_limit: 10,
  max_age_days: 30,
  keep_snapshots: 3,
  pre_script: "",
  post_script: "",
}

const LIMITS = {
  preclone_limit: { min: 1, max: 50 },
  max_age_days: { min: 1, max: 365 },
  keep_snapshots: { min: 1, max: 10 },
}

function sameSettings(
  a: BaseSnapshotSettings,
  b: BaseSnapshotSettings
): boolean {
  return (
    a.enabled === b.enabled &&
    a.schedule === b.schedule &&
    a.preclone_limit === b.preclone_limit &&
    a.max_age_days === b.max_age_days &&
    a.keep_snapshots === b.keep_snapshots &&
    a.pre_script === b.pre_script &&
    a.post_script === b.post_script
  )
}

function formatDate(value: string | null | undefined): string | null {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleString()
}

export function BaseSnapshotPanel() {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState<BaseSnapshotSettings>(DEFAULT_SETTINGS)

  const view = useQuery({
    queryKey: ["baseSnapshot"],
    queryFn: api.getBaseSnapshot,
    refetchInterval: (query) =>
      query.state.data?.record.status === "building" ? 5000 : false,
  })

  const stored = view.data?.record.settings
  useEffect(() => {
    if (stored) setDraft(stored)
  }, [stored])

  const save = useMutation({
    mutationFn: (body: BaseSnapshotSettings) =>
      api.saveBaseSnapshotSettings(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["baseSnapshot"] })
      setError(null)
    },
    onError: (e: Error) => setError(e.message),
  })

  const rebuild = useMutation({
    mutationFn: (fromScratch: boolean) => api.rebuildBaseSnapshot(fromScratch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["baseSnapshot"] })
      setError(null)
    },
    onError: (e: Error) => setError(e.message),
  })

  if (view.isLoading) return <Skeleton className="h-56" />
  if (view.isError) {
    return (
      <p className="p-4 text-xs text-destructive">
        Could not load the nightly snapshot config: {view.error.message}
      </p>
    )
  }

  const record = view.data?.record
  const status = record?.status ?? "none"
  const dirty = stored != null && !sameSettings(draft, stored)
  const building = status === "building"
  const builtAt = formatDate(record?.built_at)
  const cloneStats = [...(view.data?.clone_stats ?? [])].sort(
    (a, b) => b.clone_count - a.clone_count
  )
  const cronMissing = stored?.enabled === true && !record?.cron_id
  const progress = record?.progress ?? {
    phase: "starting",
    completed: 0,
    total: 0,
  }
  // The capture is a single opaque call, so only the clone sweep can honestly
  // show a filling bar; everything else pulses.
  const determinate = progress.phase === "cloning" && progress.total > 0
  const progressLabel =
    progress.phase === "capturing"
      ? "Capturing the snapshot…"
      : progress.phase === "cloning"
        ? "Cloning repositories…"
        : "Starting the builder…"

  const setNumber = (key: keyof typeof LIMITS, raw: string) => {
    const parsed = Number.parseInt(raw, 10)
    if (Number.isNaN(parsed)) return
    setDraft({ ...draft, [key]: parsed })
  }

  const outOfRange = (
    Object.entries(LIMITS) as Array<
      [keyof typeof LIMITS, { min: number; max: number }]
    >
  ).some(([key, { min, max }]) => draft[key] < min || draft[key] > max)

  return (
    <div className="flex flex-col gap-6 p-4">
      <section className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <Label htmlFor="base-snapshot-enabled">Nightly rebuild</Label>
            <p className="text-xs text-muted-foreground">
              Rebuilds the shared sandbox snapshot on a schedule with the most
              used repos already cloned, so runs skip the cold clone. Off means
              every run boots the default snapshot as it does today.
            </p>
          </div>
          <Switch
            id="base-snapshot-enabled"
            checked={draft.enabled}
            onCheckedChange={(checked) =>
              setDraft({ ...draft, enabled: checked })
            }
            disabled={save.isPending}
          />
        </div>

        {draft.enabled && (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
            Every pre-cloned repo is readable by every run booting this
            snapshot, including runs targeting a different repo.
          </p>
        )}

        {cronMissing && (
          <p className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            Enabled, but the schedule could not be registered — nothing will
            rebuild automatically. Save again to retry, or use Rebuild now.
          </p>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="base-snapshot-schedule">Schedule (UTC cron)</Label>
            <Input
              id="base-snapshot-schedule"
              value={draft.schedule}
              placeholder="0 9 * * *"
              onChange={(e) => setDraft({ ...draft, schedule: e.target.value })}
              disabled={save.isPending}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="base-snapshot-limit">Repos to pre-clone</Label>
            <Input
              id="base-snapshot-limit"
              type="number"
              min={LIMITS.preclone_limit.min}
              max={LIMITS.preclone_limit.max}
              value={draft.preclone_limit}
              onChange={(e) => setNumber("preclone_limit", e.target.value)}
              disabled={save.isPending}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="base-snapshot-age">
              Drop repos unused for (days)
            </Label>
            <Input
              id="base-snapshot-age"
              type="number"
              min={LIMITS.max_age_days.min}
              max={LIMITS.max_age_days.max}
              value={draft.max_age_days}
              onChange={(e) => setNumber("max_age_days", e.target.value)}
              disabled={save.isPending}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="base-snapshot-keep">Snapshots to retain</Label>
            <Input
              id="base-snapshot-keep"
              type="number"
              min={LIMITS.keep_snapshots.min}
              max={LIMITS.keep_snapshots.max}
              value={draft.keep_snapshots}
              onChange={(e) => setNumber("keep_snapshots", e.target.value)}
              disabled={save.isPending}
            />
          </div>
        </div>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="base-snapshot-pre">Before cloning</Label>
            <p className="text-xs text-muted-foreground">
              Runs in the builder before any repo is cloned or updated. Use it
              for machine-wide setup — extra tooling, package registries.
            </p>
            <InstructionsEditor
              value={draft.pre_script}
              onChange={(v) => setDraft({ ...draft, pre_script: v })}
              language="shell"
              disabled={save.isPending}
              placeholder="#!/bin/sh&#10;# e.g. corepack enable"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="base-snapshot-post">After cloning</Label>
            <p className="text-xs text-muted-foreground">
              Runs once every repo is cloned and up to date, with the working
              directory as its cwd. This is where dependency installs go — each
              repo is at <code>.repo-cache/&lt;owner&gt;/&lt;name&gt;</code>,
              and whatever you install there is baked into the snapshot.
            </p>
            <InstructionsEditor
              value={draft.post_script}
              onChange={(v) => setDraft({ ...draft, post_script: v })}
              language="shell"
              disabled={save.isPending}
              placeholder={
                "#!/bin/sh\n" +
                "for repo in .repo-cache/*/*; do\n" +
                '  [ -f "$repo/pnpm-lock.yaml" ] && (cd "$repo" && pnpm install --frozen-lockfile)\n' +
                '  [ -f "$repo/uv.lock" ] && (cd "$repo" && uv sync)\n' +
                "done"
              }
            />
          </div>
          <p className="text-xs text-muted-foreground">
            A failing script fails the whole build — no snapshot is published,
            and runs keep using the previous one.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            disabled={!dirty || outOfRange || save.isPending}
            onClick={() => void save.mutateAsync(draft).catch(() => undefined)}
          >
            Save settings
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={
              dirty || !stored?.enabled || building || rebuild.isPending
            }
            onClick={() =>
              void rebuild.mutateAsync(false).catch(() => undefined)
            }
          >
            {building ? "Building…" : "Rebuild now"}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={
              dirty || !stored?.enabled || building || rebuild.isPending
            }
            onClick={() => {
              if (
                !window.confirm(
                  "Discard the current snapshot and rebuild from the base image? " +
                    "Every repo is re-cloned from scratch, so this is much slower " +
                    "than a normal rebuild. Runs keep using the current snapshot " +
                    "until the new one is ready."
                )
              ) {
                return
              }
              void rebuild.mutateAsync(true).catch(() => undefined)
            }}
          >
            Rebuild from scratch
          </Button>
          {dirty && (
            <span className="text-xs text-muted-foreground">
              Save before rebuilding
            </span>
          )}
        </div>

        <p className="text-xs text-muted-foreground">
          Rebuilds start from the current snapshot and only fetch what changed.
          Rebuild from scratch discards it and starts from the base sandbox
          image — use it when accumulated state has gone wrong.
        </p>

        {building && (
          <div className="space-y-1.5">
            <div className="flex items-baseline justify-between gap-2 text-xs">
              <span className="text-foreground">{progressLabel}</span>
              {determinate && (
                <span className="text-muted-foreground tabular-nums">
                  {progress.completed} / {progress.total}
                </span>
              )}
            </div>
            <div
              className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-label="Base snapshot rebuild progress"
              aria-valuemin={determinate ? 0 : undefined}
              aria-valuemax={determinate ? progress.total : undefined}
              aria-valuenow={determinate ? progress.completed : undefined}
            >
              <div
                className={
                  determinate
                    ? "h-full rounded-full bg-primary transition-[width] duration-500"
                    : "h-full w-1/3 animate-pulse rounded-full bg-primary"
                }
                style={
                  determinate
                    ? {
                        width: `${Math.round((progress.completed / progress.total) * 100)}%`,
                      }
                    : undefined
                }
              />
            </div>
            <p className="text-[11px] text-muted-foreground">
              Cloning every repo and capturing the image takes a while — this
              keeps running if you navigate away.
            </p>
          </div>
        )}

        {error && <p className="text-xs text-destructive">{error}</p>}
      </section>

      <div className="border-t border-border" />

      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium text-foreground">Latest build</p>
          <span className={`text-xs ${STATUS_CLASS[status]}`}>
            {STATUS_LABEL[status]}
          </span>
          {record?.snapshot_id && (
            <span className="text-[10px] text-muted-foreground">
              snapshot {record.snapshot_id}
            </span>
          )}
          {builtAt && (
            <span className="text-[10px] text-muted-foreground">
              built {builtAt}
            </span>
          )}
        </div>
        {status === "failed" && record?.status_message && (
          <p className="text-xs text-destructive">{record.status_message}</p>
        )}
        {(record?.repos?.length ?? 0) > 0 && (
          <p className="text-xs text-muted-foreground">
            Pre-cloned: {record?.repos?.join(", ")}
          </p>
        )}
        {(record?.failed_repos?.length ?? 0) > 0 && (
          <p className="text-xs text-destructive">
            Failed to clone: {record?.failed_repos?.join(", ")}
          </p>
        )}
        {(view.data?.next_preclone.length ?? 0) > 0 && (
          <p className="text-xs text-muted-foreground">
            Next build will pre-clone: {view.data?.next_preclone.join(", ")}
          </p>
        )}
      </section>

      <div className="border-t border-border" />

      <section className="space-y-2">
        <p className="text-sm font-medium text-foreground">
          Repositories Open SWE has cloned
        </p>
        {cloneStats.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Nothing recorded yet. Repos appear here after a run preps a sandbox
            for them.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1 pr-4 font-medium">Repository</th>
                  <th className="py-1 pr-4 font-medium">Runs</th>
                  <th className="py-1 font-medium">Last used</th>
                </tr>
              </thead>
              <tbody>
                {cloneStats.map((stat) => (
                  <tr key={stat.full_name} className="border-t border-border">
                    <td className="py-1 pr-4">{stat.full_name}</td>
                    <td className="py-1 pr-4">{stat.clone_count}</td>
                    <td className="py-1 text-muted-foreground">
                      {formatDate(stat.last_cloned_at) ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
