import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { api, type ReviewStyle } from "@/lib/api"

const stylesKey = ["reviewStyles"] as const

async function ensureStyle(repository: string, current?: ReviewStyle) {
  return current ?? api.createReviewStyle(repository)
}

export function RepositoryInstructionsPanel({
  repository,
  canEdit,
  onDirtyChange,
}: {
  repository: string
  canEdit: boolean
  onDirtyChange?: (dirty: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState("")
  const [observedPrompt, setObservedPrompt] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const styles = useQuery({
    queryKey: stylesKey,
    queryFn: api.listReviewStyles,
    refetchInterval: (query) =>
      query.state.data?.some(
        (item) => item.full_name === repository && item.status === "running"
      )
        ? 4000
        : false,
  })
  const active = styles.data?.find((item) => item.full_name === repository)
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange])

  const savedPrompt = active?.custom_prompt ?? ""
  if (styles.data && !dirty && observedPrompt !== savedPrompt) {
    setObservedPrompt(savedPrompt)
    setDraft(savedPrompt)
  }

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: stylesKey })
  }
  const save = useMutation({
    mutationFn: async () => {
      if (!draft.trim()) {
        if (active) await api.deleteReviewStyle(repository)
        return null
      }
      await ensureStyle(repository, active)
      return api.saveReviewStylePrompt(repository, draft)
    },
    onSuccess: (saved) => {
      queryClient.setQueryData<Array<ReviewStyle>>(
        stylesKey,
        (current = []) => [
          ...current.filter((item) => item.full_name !== repository),
          ...(saved ? [saved] : []),
        ]
      )
      setDirty(false)
      setObservedPrompt(saved?.custom_prompt ?? "")
      setNotice(saved ? "Instructions saved." : "Instructions reset.")
      setError(null)
    },
    onError: (failure: Error) => setError(failure.message),
  })
  const analyze = useMutation({
    mutationFn: async () => {
      await ensureStyle(repository, active)
      return api.analyzeReviewStyle(repository)
    },
    onSuccess: async (record) => {
      queryClient.setQueryData<Array<ReviewStyle>>(
        stylesKey,
        (current = []) => [
          ...current.filter((item) => item.full_name !== repository),
          record,
        ]
      )
      await refresh()
    },
    onError: (failure: Error) => setError(failure.message),
  })
  const cancel = useMutation({
    mutationFn: () => api.cancelReviewStyle(repository),
    onSuccess: () => void refresh(),
    onError: (failure: Error) => setError(failure.message),
  })

  if (styles.isLoading) return <Skeleton className="h-64 w-full" />
  if (styles.isError) {
    return (
      <p role="alert" className="p-4 text-sm text-destructive">
        Could not load repository instructions. {styles.error.message}
      </p>
    )
  }

  const running = active?.status === "running"
  return (
    <div className="space-y-5 p-4">
      <div className="space-y-2">
        <Label htmlFor="repository-review-instructions">
          Repository review instructions
        </Label>
        <p className="text-xs text-muted-foreground">
          These instructions apply only to {repository} and take precedence over
          the shared review guidelines.
        </p>
        <Textarea
          id="repository-review-instructions"
          className="min-h-64 font-mono text-xs"
          value={draft}
          disabled={!canEdit || running || save.isPending || analyze.isPending}
          placeholder="Add repository-specific review guidance."
          onChange={(event) => {
            setDraft(event.target.value)
            setDirty(true)
            setNotice(null)
            setError(null)
          }}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={
            !canEdit || !dirty || save.isPending || analyze.isPending || running
          }
          onClick={() => save.mutate()}
        >
          {save.isPending
            ? "Saving…"
            : draft.trim()
              ? "Save instructions"
              : "Reset instructions"}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={!canEdit || running || save.isPending || analyze.isPending}
          onClick={() => analyze.mutate()}
        >
          {running ? "Analyzing…" : "Run analysis"}
        </Button>
        {canEdit && running && (
          <Button
            size="sm"
            variant="outline"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
          >
            Cancel analysis
          </Button>
        )}
        {active && <Badge variant="outline">{active.status}</Badge>}
      </div>
      <p className="text-xs text-muted-foreground">
        Analysis learns from recent GitHub review feedback and replaces the
        saved repository instructions when it completes. Copy any text you want
        to keep before starting it.
      </p>
      <p className="text-xs text-muted-foreground">
        Reset removes this repository customization, its analysis history, and
        ongoing learning schedule.
      </p>
      {dirty && active?.custom_prompt !== draft && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setDraft(active?.custom_prompt ?? "")
            setObservedPrompt(active?.custom_prompt ?? "")
            setDirty(false)
          }}
        >
          Reload saved instructions
        </Button>
      )}
      {active?.analysis_summary && (
        <p className="text-xs text-muted-foreground">
          {active.analysis_summary}
        </p>
      )}
      {notice && (
        <p role="status" className="text-xs">
          {notice}
        </p>
      )}
      {(error || active?.error) && (
        <p role="alert" className="text-xs text-destructive">
          {error ?? active?.error}
        </p>
      )}
    </div>
  )
}
