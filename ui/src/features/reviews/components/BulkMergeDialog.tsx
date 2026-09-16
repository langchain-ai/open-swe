import { useQueries } from "@tanstack/react-query"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { api, type MergeMethod, type OpenPullRequest } from "@/lib/api"
import {
  mergeMethodLabels,
  mergeMethods,
  readPreferredMergeMethod,
} from "../lib/mergeMethod"
import { pullRequestKey } from "../lib/status"
import { control } from "../lib/styles"

export function BulkMergeDialog({
  pullRequests,
  onCancel,
  onConfirm,
}: {
  pullRequests: OpenPullRequest[]
  onCancel: () => void
  onConfirm: (method: MergeMethod) => void
}) {
  const [choice, setChoice] = useState<MergeMethod | "">(
    () => readPreferredMergeMethod() ?? ""
  )
  const repos = [...new Set(pullRequests.map((pr) => pr.repo))]
  const settings = useQueries({
    queries: repos.map((repo) => ({
      queryKey: ["repo-merge-methods", repo],
      queryFn: () => api.repoMergeMethods(repo),
      staleTime: Infinity,
      retry: false,
    })),
  })
  const loading = settings.some((setting) => setting.isPending)
  const failure = settings.find((setting) => setting.error)?.error
  const shared = mergeMethods.filter((method) =>
    settings.every((setting) => setting.data?.mergeMethods.includes(method))
  )
  const only = shared.length === 1 ? shared[0] : undefined
  // A remembered method the selected repositories do not all allow is no choice.
  const method =
    only ?? (shared.includes(choice as MergeMethod) ? choice : undefined)
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
    >
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>
            Merge {pullRequests.length} pull request
            {pullRequests.length === 1 ? "" : "s"}?
          </DialogTitle>
          <DialogDescription>
            {pullRequests.map(pullRequestKey).join(", ")}
          </DialogDescription>
        </DialogHeader>
        {loading ? (
          <p role="status">Loading merge settings…</p>
        ) : failure ? (
          <p role="alert" className="text-destructive">
            {failure.message}
          </p>
        ) : shared.length === 0 ? (
          <p role="alert">The selected repositories share no merge method.</p>
        ) : only ? (
          <p>Merge method: {mergeMethodLabels[only]}</p>
        ) : (
          <select
            className={control}
            aria-label="Merge method"
            value={choice}
            onChange={(event) => {
              const value = event.target.value
              if (mergeMethods.some((option) => option === value))
                setChoice(value as MergeMethod)
            }}
          >
            <option value="" disabled>
              Choose a merge method
            </option>
            {shared.map((option) => (
              <option key={option} value={option}>
                {mergeMethodLabels[option]}
              </option>
            ))}
          </select>
        )}
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!method}
            onClick={() => {
              if (method) onConfirm(method)
            }}
          >
            Merge pull requests
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
