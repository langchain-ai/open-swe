import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import {
  QueueList,
  QueueRow,
} from "@langchain/gtm-platform-design-system/patterns/queue-row"
import { RecordHeader } from "@langchain/gtm-platform-design-system/patterns/record-header"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxTrigger,
} from "@langchain/gtm-platform-design-system/ui/combobox"
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
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"

import { AlertTriangle, Brush, Key, Play, X } from "@/components/glyphs"
import type { ReviewApprovalMode, ReviewStyle } from "@/lib/api"
import { api, isGithubReauthError, loginUrl } from "@/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { optimisticUpdate } from "@/lib/optimistic"
import { useRepos } from "@/lib/profile"
import { normalizeRepoFullName } from "@/lib/repo"
import { useSession } from "@/lib/session"

const APPROVAL_MODES: Array<{ value: ReviewApprovalMode; label: string }> = [
  { value: "off", label: "Off" },
  { value: "dry_run", label: "Dry run" },
  { value: "approve", label: "Approve" },
]

function StatusBadge({ status }: { status: ReviewStyle["status"] }) {
  const tone =
    status === "completed"
      ? "positive"
      : status === "running"
        ? "attention"
        : status === "failed"
          ? "risk"
          : "neutral"
  return (
    <Badge tier="quiet" tone={tone} dot={status === "running"}>
      {status}
    </Badge>
  )
}

export function ReviewStylesPanel() {
  const session = useSession()
  const qc = useQueryClient()
  const [addRepo, setAddRepo] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [draftPrompt, setDraftPrompt] = useState("")

  const styles = useQuery({
    queryKey: ["reviewStyles"],
    queryFn: api.listReviewStyles,
    meta: { invalidatedBy: [invalidationTopic("review-styles")] },
  })

  const repos = useRepos()

  const detail = useQuery({
    queryKey: ["reviewStyle", selected],
    queryFn: () => api.getReviewStyle(selected!),
    enabled: !!selected,
    meta: {
      invalidatedBy: selected
        ? [invalidationTopic("review-styles", selected)]
        : [],
    },
  })

  const loadedRepo = detail.data?.full_name
  const loadedPrompt = detail.data?.custom_prompt
  useEffect(() => {
    if (loadedRepo === undefined) return
    // oxlint-disable-next-line react/set-state-in-effect
    setDraftPrompt(loadedPrompt ?? "")
  }, [loadedPrompt, loadedRepo])

  const approvalsFile = useQuery({
    queryKey: ["reviewStyleApprovalsFile", selected],
    queryFn: () => api.getApprovalsFile(selected!),
    enabled: !!selected,
  })

  const saveMode = useMutation({
    mutationFn: ({
      repo,
      mode,
    }: {
      repo: string
      mode: ReviewApprovalMode | null
    }) => api.saveReviewApprovalMode(repo, mode),
    meta: { errorTitle: "Couldn't save approval mode" },
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
    return (
      <Stack gap="md">
        <Skeleton className="h-24 w-full rounded-panel" />
        <Skeleton className="h-40 w-full rounded-panel" />
      </Stack>
    )
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
    [saveMode, createStyle, analyze, savePrompt, cancelAnalysis, removeStyle]
      .map((m) => m.error)
      .some(isGithubReauthError)

  return (
    <Stack gap="xl">
      {githubReauth && (
        <StateNotice
          tone="ATTENTION"
          icon={Key}
          title="Your GitHub connection expired"
          description="Sign in with GitHub again to list installed repos and run style analysis."
          action={
            <Button
              size="compact"
              variant="outline"
              nativeButton={false}
              render={<a href={loginUrl()} />}
            >
              Sign in with GitHub again
            </Button>
          }
        />
      )}
      <PageSection title="Add repository" contained inset="padded">
        <Stack gap="sm">
          <Label htmlFor="add-repo">Repository</Label>
          <Inline gap="sm" align="start" wrap>
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
              className="min-w-48 flex-1"
            />
            <Button
              disabled={!canAdd}
              loading={createStyle.isPending}
              onClick={handleAdd}
            >
              Add
            </Button>
          </Inline>
          {suggestedRepos.length > 0 && (
            <Combobox value={addRepo} onValueChange={(v) => setAddRepo(v)}>
              <ComboboxTrigger
                placeholder="Or pick an installed repo…"
                aria-label="Pick an installed repo"
              >
                {suggestedRepos.some((r) => r.full_name === addRepo)
                  ? addRepo
                  : null}
              </ComboboxTrigger>
              <ComboboxContent>
                <ComboboxInput placeholder="Search installed repos…" />
                <ComboboxList>
                  <ComboboxEmpty>No matches</ComboboxEmpty>
                  {suggestedRepos.map((r) => (
                    <ComboboxItem key={r.full_name} value={r.full_name}>
                      <span className="truncate" title={r.full_name}>
                        {r.full_name}
                      </span>
                      {r.private && (
                        <span className="ml-auto text-meta text-ink-subtle">
                          private
                        </span>
                      )}
                    </ComboboxItem>
                  ))}
                </ComboboxList>
              </ComboboxContent>
            </Combobox>
          )}
        </Stack>
      </PageSection>

      <PageSection title="Repositories" contained>
        {(styles.data ?? []).length === 0 ? (
          <EmptyState
            icon={Brush}
            title="No repositories yet."
            description="Add a repository to learn its review style."
          />
        ) : (
          <QueueList label="Repositories with review styles">
            {(styles.data ?? []).map((s) => (
              <QueueRow
                key={s.full_name}
                primary={s.full_name}
                state={<StatusBadge status={s.status} />}
                selected={selected === s.full_name}
                onSelect={() => setSelected(s.full_name)}
              />
            ))}
          </QueueList>
        )}
      </PageSection>

      {!selected || !active ? (
        <p className="text-meta text-ink-subtle">
          Select a repository above to view or edit its review style prompt.
        </p>
      ) : (
        <Stack render={<section aria-label={active.full_name} />} gap="lg">
          <RecordHeader
            title={active.full_name}
            status={<StatusBadge status={active.status} />}
            meta={
              <Stack gap="xs" className="text-meta text-ink-subtle">
                {(active.top_reviewers.length > 0 ||
                  active.prs_sampled > 0) && (
                  <Inline gap="md" wrap>
                    {active.top_reviewers.length > 0 && (
                      <span>Reviewers: {active.top_reviewers.join(", ")}</span>
                    )}
                    {active.prs_sampled > 0 && (
                      <span className="tabular-nums">
                        {active.prs_sampled} PRs · {active.reviews_sampled}{" "}
                        reviews sampled
                      </span>
                    )}
                  </Inline>
                )}
                {active.analysis_summary && <p>{active.analysis_summary}</p>}
              </Stack>
            }
            actions={
              <>
                <Button
                  size="compact"
                  variant="outline"
                  disabled={active.status === "running" || analyze.isPending}
                  onClick={() => {
                    void analyze
                      .mutateAsync(active.full_name)
                      .catch(() => undefined)
                  }}
                >
                  <Icon icon={Play} size="sm" />
                  {active.status === "running" ? "Analyzing…" : "Run analysis"}
                </Button>
                {active.status === "running" && (
                  <Button
                    size="compact"
                    variant="ghost"
                    disabled={cancelAnalysis.isPending}
                    onClick={() => cancelAnalysis.mutate(active.full_name)}
                  >
                    Cancel
                  </Button>
                )}
                <ConfirmableAction
                  title={`Remove ${active.full_name}?`}
                  description="Its review style prompt and approval mode are deleted. This cannot be undone."
                  confirmLabel="Remove repository"
                  onConfirm={async () => {
                    removeStyle.mutate(active.full_name)
                  }}
                  trigger={
                    <Button
                      size="compact"
                      variant="ghost"
                      disabled={
                        removeStyle.isPending ||
                        (!!active.approval_mode && !session.data?.is_admin)
                      }
                    >
                      <Icon icon={X} size="sm" />
                      Remove
                    </Button>
                  }
                />
              </>
            }
          />
          {active.error && (
            <StateNotice
              tone="RISK"
              icon={AlertTriangle}
              title="The last analysis failed"
              description={active.error}
            />
          )}
          <PageSection
            title="Review style prompt"
            actions={
              <Button
                size="compact"
                disabled={!draftPrompt.trim()}
                loading={savePrompt.isPending}
                onClick={() =>
                  savePrompt.mutate({
                    full_name: active.full_name,
                    custom_prompt: draftPrompt,
                  })
                }
              >
                Save prompt
              </Button>
            }
          >
            <Textarea
              aria-label="Review style prompt"
              className="min-h-80 w-full font-mono text-label"
              value={draftPrompt}
              onChange={(e) => setDraftPrompt(e.target.value)}
              placeholder={
                active.status === "running"
                  ? "Analysis in progress…"
                  : "Run analysis or write a custom prompt for this repository."
              }
              disabled={active.status === "running"}
            />
          </PageSection>
          <Stack gap="sm">
            <Label htmlFor="repo-approval-mode">Approval mode</Label>
            <p className="text-meta text-ink-subtle">
              Criteria come from <code>.open-swe/APPROVALS.md</code> in the
              repository, read from each pull request&apos;s base branch. Dry
              run posts the assessment without approving; Approve submits a
              GitHub approval when the assessment passes. Nothing is merged.
            </p>
            <Select
              items={APPROVAL_MODES}
              value={detail.data?.approval_mode ?? "dry_run"}
              onValueChange={(mode) => {
                if (!mode) return
                saveMode.mutate({ repo: active.full_name, mode })
              }}
              disabled={
                !session.data?.is_admin || !detail.data || saveMode.isPending
              }
            >
              <SelectTrigger id="repo-approval-mode" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {APPROVAL_MODES.map((mode) => (
                  <SelectItem key={mode.value} value={mode.value}>
                    {mode.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {approvalsFile.data && (
              <p className="text-meta text-ink-subtle">
                {approvalsFile.data.found ? (
                  <>
                    <code>.open-swe/APPROVALS.md</code> found on the default
                    branch.
                  </>
                ) : (
                  <>
                    No <code>.open-swe/APPROVALS.md</code> on the default
                    branch, so reviews post no approval assessment.
                  </>
                )}
              </p>
            )}
          </Stack>
        </Stack>
      )}
    </Stack>
  )
}
