import { Link as MacawLink } from "@langchain/macaw-components/Link"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Select } from "@langchain/macaw-components/Select"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { AddRepositoryField } from "@/components/AddRepositoryField"
import type { ReviewApprovalMode, ReviewStyle } from "@/lib/api"
import { api, isGithubReauthError, loginUrl } from "@/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { optimisticUpdate } from "@/lib/optimistic"
import { useRepos } from "@/lib/profile"
import { normalizeRepoFullName } from "@/lib/repo"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

const APPROVAL_MODES: Array<{ value: ReviewApprovalMode; label: string }> = [
  { value: "off", label: "Off" },
  { value: "dry_run", label: "Dry run" },
  { value: "approve", label: "Approve" },
]

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
    [saveMode, createStyle, savePrompt, removeStyle]
      .map((m) => m.error)
      .some(isGithubReauthError)

  return (
    <div className="flex flex-col gap-space-5 p-space-4">
      {githubReauth && (
        <Banner intent="error">
          <span className="text-xs text-error-secondary">
            Your GitHub connection expired.{" "}
            <MacawLink href={loginUrl()} variant="sm">
              Sign in with GitHub again
            </MacawLink>{" "}
            to list installed repos.
          </span>
        </Banner>
      )}
      <section className="space-y-space-4">
        <AddRepositoryField
          id="add-repo"
          value={addRepo}
          onChange={setAddRepo}
          suggestions={suggestedRepos}
          canAdd={canAdd && !createStyle.isPending}
          onAdd={handleAdd}
        />

        <div className="space-y-space-2">
          <p className="text-xs font-medium text-primary">Repositories</p>
          {(styles.data ?? []).length === 0 ? (
            <p className="text-xs text-secondary">No repositories yet.</p>
          ) : (
            <ul className="flex flex-wrap gap-space-2">
              {(styles.data ?? []).map((s) => (
                <li key={s.full_name}>
                  <button
                    type="button"
                    aria-pressed={selected === s.full_name}
                    className={cn(
                      "inline-flex max-w-full items-center gap-space-2 rounded-md border px-space-2 py-space-1 text-left text-xs transition-colors hover:bg-surface-level-1-hover",
                      selected === s.full_name
                        ? "border-brand bg-selected font-medium"
                        : "border-default"
                    )}
                    onClick={() => setSelected(s.full_name)}
                  >
                    <span className="truncate">{s.full_name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <div className="border-t border-default" />

      <section className="space-y-space-3">
        {!selected || !active ? (
          <p className="text-xs text-secondary">
            Select a repository above to view or edit its review style prompt.
          </p>
        ) : (
          <>
            <p className="text-sm font-medium text-primary">
              {active.full_name}
            </p>
            <div className="flex flex-wrap gap-space-2">
              <Button
                color="primary"
                size="xs"
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
                color="error"
                size="xs"
                disabled={
                  removeStyle.isPending ||
                  (!!active.approval_mode && !session.data?.is_admin)
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
              inputClassName="min-h-[320px] font-mono text-xs"
              resize="none"
              size="md"
              value={draftPrompt}
              onChange={setDraftPrompt}
              placeholder="Write a custom prompt for this repository."
            />
            <Text as="h3" variant="sm" weight="medium">
              Approval Mode
            </Text>
            <p className="text-xs text-secondary">
              Criteria come from <code>.open-swe/APPROVALS.md</code> in the
              repository, read from each pull request&apos;s base branch. Dry
              run posts the assessment without approving; Approve submits a
              GitHub approval when the assessment passes. Nothing is merged.
            </p>
            <Select<ReviewApprovalMode>
              aria-label="Approval mode"
              hideSearch
              options={APPROVAL_MODES}
              size="md"
              triggerClassName="w-40"
              value={detail.data?.approval_mode ?? "dry_run"}
              onChange={(mode) => {
                if (!mode) return
                saveMode.mutate({ repo: active.full_name, mode })
              }}
              disabled={
                !session.data?.is_admin || !detail.data || saveMode.isPending
              }
            />
            {approvalsFile.data && (
              <p className="text-xs text-secondary">
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
          </>
        )}
      </section>
    </div>
  )
}
