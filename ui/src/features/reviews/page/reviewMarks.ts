import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"
import { toast } from "sonner"

import { api, type ReviewedFile } from "@/lib/api"
import { useSession } from "@/lib/session"
import { reviewKeys } from "@/features/reviews/lib/reviewKeys"
import type { DiffEntry } from "./diffEntries"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"

/** The lines a Viewed mark covers: the whole file, or only its slice's hunks. */
function reviewedFile(entry: DiffEntry): ReviewedFile {
  if (entry.readKey === entry.file.path) return { path: entry.file.path }
  return {
    path: entry.file.path,
    hunks: entry.fileDiff.hunks.map((hunk) => ({
      addition_start: hunk.additionStart,
      addition_count: hunk.additionCount,
      deletion_start: hunk.deletionStart,
      deletion_count: hunk.deletionCount,
    })),
  }
}

/**
 * Keeps this person's Viewed marks on the server, so who reviewed what is
 * shared, and marks files viewed that they already reviewed elsewhere.
 */
export function useReviewMarks(pr: PullRequestRef) {
  const queryClient = useQueryClient()
  const login = useSession().data?.login?.toLowerCase()
  const coverage = useQuery(reviewQueries.coverage(pr)).data
  const viewedSha = useReviewPage((state) => state.viewedSha)
  const setReadRecorder = useReviewPage((state) => state.setReadRecorder)
  const restoreRead = useReviewPage((state) => state.restoreRead)
  const adoptViewed = useReviewPage((state) => state.adoptViewed)
  const { owner, repo, number } = pr

  useEffect(() => {
    setReadRecorder((entries, read) => {
      const headSha = entries[0]?.file.headSha
      if (!headSha) return
      api
        .markReviewedLines(owner, repo, number, {
          head_sha: headSha,
          files: entries.map(reviewedFile),
          reviewed: read,
        })
        .then(() =>
          queryClient.invalidateQueries({
            queryKey: reviewKeys.coverage({ owner, repo, number }),
          })
        )
        .catch((error: unknown) => {
          restoreRead(entries, !read)
          toast.error("Couldn't save what you reviewed", {
            description: error instanceof Error ? error.message : undefined,
          })
        })
    })
    return () => setReadRecorder(null)
  }, [owner, repo, number, queryClient, setReadRecorder, restoreRead])

  useEffect(() => {
    if (!coverage || !login || coverage.head_sha !== viewedSha) return
    adoptViewed(
      coverage.files
        .filter(
          (file) =>
            file.changed_lines > 0 &&
            file.reviewers.some(
              (reviewer) =>
                reviewer.login === login &&
                reviewer.lines === file.changed_lines
            )
        )
        .map((file) => file.path)
    )
  }, [coverage, login, viewedSha, adoptViewed])
}
