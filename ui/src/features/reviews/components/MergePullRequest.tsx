import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { api, type MergeMethod, type OpenPullRequest } from "@/lib/api"
import { control } from "../lib/styles"
import {
  mergeMethodLabels,
  mergeMethods,
  readPreferredMergeMethod,
  writePreferredMergeMethod,
} from "../lib/mergeMethod"

function asMergeMethod(value: string): MergeMethod | "" | null {
  if (value === "") return ""
  return mergeMethods.find((method) => method === value) ?? null
}

export function MergePullRequest({
  pr,
  onMerged,
}: {
  pr: OpenPullRequest
  onMerged: () => void
}) {
  const [method, setMethod] = useState<MergeMethod | "">(
    () => readPreferredMergeMethod() ?? ""
  )
  const merge = useMutation({
    mutationFn: async () => {
      if (!method) throw new Error("Choose a merge method.")
      const result = await api.mergePullRequest(pr, method)
      if (!result.merged) throw new Error("GitHub did not confirm the merge.")
      writePreferredMergeMethod(method)
    },
    onSuccess: () => {
      toast.success(`Merged ${pr.repo}#${pr.number}`)
      onMerged()
    },
    onError: (error) =>
      toast.error(`Could not merge ${pr.repo}#${pr.number}`, {
        description: error.message,
      }),
    retry: false,
  })
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        className={control}
        aria-label={`Merge method for PR #${pr.number}`}
        value={method}
        disabled={merge.isPending}
        onChange={(event) => {
          const chosen = asMergeMethod(event.target.value)
          if (chosen !== null) setMethod(chosen)
        }}
      >
        <option value="" disabled>
          Merge method
        </option>
        {mergeMethods.map((option) => (
          <option key={option} value={option}>
            {mergeMethodLabels[option]}
          </option>
        ))}
      </select>
      <Button
        size="sm"
        variant="outline"
        disabled={!method || !pr.headSha || merge.isPending || merge.isSuccess}
        aria-live="polite"
        onClick={() => merge.mutate()}
      >
        {merge.isPending ? "Merging…" : merge.isError ? "Retry merge" : "Merge"}
      </Button>
      {merge.error && (
        <p role="alert" className="text-destructive">
          {merge.error.message}
        </p>
      )}
    </div>
  )
}
