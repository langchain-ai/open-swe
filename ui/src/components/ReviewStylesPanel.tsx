import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import type { ReviewStyle } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
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
import { Textarea } from "@/components/ui/textarea"
import { api, isGithubReauthError, loginUrl } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { useRepos } from "@/lib/profile"
import { normalizeRepoFullName } from "@/lib/repo"
import { useSession } from "@/lib/session"

function statusVariant(status: ReviewStyle["status"]) {
  switch (status) {
    case "completed":
      return "default" as const
    case "running":
      return "secondary" as const
    case "failed":
      return "destructive" as const
    default:
      return "outline" as const
  }
}

export function ReviewStylesPanel() {
  const session = useSession()
  const qc = useQueryClient()
  const [addRepo, setAddRepo] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [draftPrompt, setDraftPrompt] = useState("")
  const [draftPolicy, setDraftPolicy] = useState("")

  const styles = useQuery({
    queryKey: ["reviewStyles"],
    queryFn: api.listReviewStyles,
    refetchInterval: (q) => {
      const hasRunning = (q.state.data ?? []).some(
        (r) => r.status === "running"
      )
      return hasRunning ? 4000 : false
    },
  })

  const repos = useRepos()

  const detail = useQuery({
    queryKey: ["reviewStyle", selected],
    queryFn: () => api.getReviewStyle(selected!),
    enabled: !!selected,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 4000 : false),
  })

  const loadedRepo = detail.data?.full_name
  const loadedPrompt = detail.data?.custom_prompt
  useEffect(() => {
    if (loadedRepo === undefined) return
    // oxlint-disable-next-line react/set-state-in-effect
    setDraftPrompt(loadedPrompt ?? "")
  }, [loadedPrompt, loadedRepo])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setDraftPolicy(detail.data?.approval_policy ?? "")
  }, [detail.data?.approval_policy, detail.data?.full_name])

  const savePolicy = useMutation({
    mutationFn: ({ repo, policy }: { repo: string; policy: string | null }) =>
      api.saveReviewApprovalPolicy(repo, policy),
    meta: { errorTitle: "Couldn't save approval policy" },
    onSuccess: (record) => {
      qc.setQueryData(["reviewStyle", record.full_name], record)
      void qc.invalidateQueries({ queryKey: ["reviewStyles"] })
    },
  })

  const createStyle = useMutation({
    mutationFn: (full_name: string) => api.createReviewStyle(full_name),
    meta: { errorTitle: "Couldn't add repository" },
    onSuccess: (record) => {
      void qc.invalidateQueries({ queryKey: ["reviewStyles"] })
      setSelected(record.full_name)
    },
  })

  const analyze = useMutation({
    mutationFn: (full_name: string) => api.analyzeReviewStyle(full_name),
    meta: { errorTitle: "Couldn't start analysis" },
    onMutate: async (full_name) => {
      await Promise.all([
        qc.cancelQueries({ queryKey: ["reviewStyles"] }),
        qc.cancelQueries({ queryKey: ["reviewStyle", full_name] }),
      ])
      const snapshot = {
        list: qc.getQueryData<Array<ReviewStyle>>(["reviewStyles"]),
        detail: qc.getQueryData<ReviewStyle>(["reviewStyle", full_name]),
      }
      const running = (style: ReviewStyle): ReviewStyle =>
        style.full_name === full_name
          ? { ...style, status: "running", error: null }
          : style
      qc.setQueryData<Array<ReviewStyle>>(["reviewStyles"], (old) =>
        old?.map(running)
      )
      qc.setQueryData<ReviewStyle>(["reviewStyle", full_name], (old) =>
        old ? running(old) : old
      )
      return snapshot
    },
    onSuccess: (record) => {
      qc.setQueryData(["reviewStyle", record.full_name], record)
    },
    onError: (_e, full_name, snapshot) => {
      qc.setQueryData(["reviewStyles"], snapshot?.list)
      qc.setQueryData(["reviewStyle", full_name], snapshot?.detail)
    },
    onSettled: (_record, _error, full_name) => {
      void qc.invalidateQueries({ queryKey: ["reviewStyles"] })
      void qc.invalidateQueries({ queryKey: ["reviewStyle", full_name] })
    },
  })

  const savePrompt = useMutation({
    mutationFn: ({
      full_name,
      custom_prompt,
    }: {
      full_name: string
      custom_prompt: string
    }) => api.saveReviewStylePrompt(full_name, custom_prompt),
    meta: { errorTitle: "Couldn't save prompt" },
    onSuccess: (record) => {
      qc.setQueryData(["reviewStyle", record.full_name], record)
      void qc.invalidateQueries({ queryKey: ["reviewStyles"] })
    },
  })

  const cancelAnalysis = useMutation({
    mutationFn: (full_name: string) => api.cancelReviewStyle(full_name),
    meta: { errorTitle: "Couldn't cancel analysis" },
    onSuccess: (record) => {
      qc.setQueryData(["reviewStyle", record.full_name], record)
      void qc.invalidateQueries({ queryKey: ["reviewStyles"] })
    },
  })

  const removeStyle = useMutation({
    mutationFn: (full_name: string) => api.deleteReviewStyle(full_name),
    meta: { errorTitle: "Couldn't remove repository" },
    onMutate: async (full_name) => ({
      undo: await optimisticUpdate<Array<ReviewStyle>>(
        qc,
        ["reviewStyles"],
        (old) => old.filter((style) => style.full_name !== full_name)
      ),
    }),
    onSuccess: (_data, full_name) => {
      if (selected === full_name) {
        setSelected(null)
        setDraftPrompt("")
      }
    },
    onError: (_e, _full_name, ctx) => ctx?.undo(),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["reviewStyles"] })
    },
  })

  if (styles.isLoading) {
    return <Skeleton className="h-40" />
  }

  const configured = new Set((styles.data ?? []).map((s) => s.full_name))
  const suggestedRepos = (repos.data?.repositories ?? []).filter(
    (r) => !configured.has(r.full_name)
  )
  const normalizedAddRepo = normalizeRepoFullName(addRepo)
  const canAdd =
    normalizedAddRepo !== null && !configured.has(normalizedAddRepo)
  const active =
    detail.data ?? styles.data?.find((s) => s.full_name === selected) ?? null

  const handleAdd = () => {
    if (!normalizedAddRepo || !canAdd) return
    void createStyle
      .mutateAsync(normalizedAddRepo)
      .then(() => setAddRepo(""))
      .catch(() => undefined)
  }

  const githubReauth =
    (repos.isError && isGithubReauthError(repos.error)) ||
    [savePolicy, createStyle, analyze, savePrompt, cancelAnalysis, removeStyle]
      .map((m) => m.error)
      .some(isGithubReauthError)

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
          to list installed repos and run style analysis.
        </div>
      )}
      <section className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="add-repo">Add repository</Label>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <Input
              id="add-repo"
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
              disabled={!canAdd || createStyle.isPending}
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
          {(styles.data ?? []).length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No repositories yet.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {(styles.data ?? []).map((s) => (
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
                    <Badge
                      variant={statusVariant(s.status)}
                      className="shrink-0"
                    >
                      {s.status}
                    </Badge>
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
            Select a repository above to view or edit its review style prompt.
          </p>
        ) : (
          <>
            <p className="text-sm font-medium text-foreground">
              {active.full_name}
            </p>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge variant={statusVariant(active.status)}>
                {active.status}
              </Badge>
              {active.top_reviewers.length > 0 && (
                <span className="text-muted-foreground">
                  Reviewers: {active.top_reviewers.join(", ")}
                </span>
              )}
              {active.prs_sampled > 0 && (
                <span className="text-muted-foreground">
                  {active.prs_sampled} PRs · {active.reviews_sampled} reviews
                  sampled
                </span>
              )}
            </div>
            {active.analysis_summary && (
              <p className="text-xs text-muted-foreground">
                {active.analysis_summary}
              </p>
            )}
            {active.error && (
              <p className="text-xs text-destructive">{active.error}</p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={active.status === "running" || analyze.isPending}
                onClick={() => {
                  void analyze
                    .mutateAsync(active.full_name)
                    .catch(() => undefined)
                }}
              >
                {active.status === "running" ? "Analyzing…" : "Run analysis"}
              </Button>
              {active.status === "running" && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={cancelAnalysis.isPending}
                  onClick={() => cancelAnalysis.mutate(active.full_name)}
                >
                  Cancel
                </Button>
              )}
              <Button
                size="sm"
                disabled={!draftPrompt.trim() || savePrompt.isPending}
                onClick={() =>
                  savePrompt.mutate({
                    full_name: active.full_name,
                    custom_prompt: draftPrompt,
                  })
                }
              >
                Save prompt
              </Button>
              <Button
                size="sm"
                variant="destructive"
                disabled={
                  removeStyle.isPending ||
                  (!!active.approval_policy && !session.data?.is_admin)
                }
                onClick={() => {
                  if (
                    !window.confirm(
                      `Remove ${active.full_name} from review style prompts? This cannot be undone.`
                    )
                  ) {
                    return
                  }
                  removeStyle.mutate(active.full_name)
                }}
              >
                Remove
              </Button>
            </div>
            <Textarea
              className="min-h-[320px] w-full font-mono text-xs"
              value={draftPrompt}
              onChange={(e) => setDraftPrompt(e.target.value)}
              placeholder={
                active.status === "running"
                  ? "Analysis in progress…"
                  : "Run analysis or write a custom prompt for this repository."
              }
              disabled={active.status === "running"}
            />
            <Label htmlFor="repo-approval-policy">
              Approval policy override
            </Label>
            <p className="text-xs text-muted-foreground">
              Replaces the shared approval policy for this repository. Leave
              blank to inherit. Review style analysis does not change this
              policy.
            </p>
            <Textarea
              id="repo-approval-policy"
              className="min-h-[160px] w-full font-mono text-xs"
              value={draftPolicy}
              onChange={(e) => setDraftPolicy(e.target.value)}
              placeholder="Inherit the shared approval policy"
              maxLength={10000}
              disabled={!session.data?.is_admin || !detail.data}
            />
            {session.data?.is_admin && (
              <Button
                size="sm"
                disabled={
                  !detail.data ||
                  savePolicy.isPending ||
                  draftPolicy.trim() ===
                    (detail.data.approval_policy ?? "").trim()
                }
                onClick={() =>
                  savePolicy.mutate({
                    repo: active.full_name,
                    policy: draftPolicy.trim() || null,
                  })
                }
              >
                Save approval policy
              </Button>
            )}
          </>
        )}
      </section>
    </div>
  )
}
