import { api, type ReviewDiffFile, type ReviewFileContents } from "@/lib/api"

// Patch-parsed diffs only carry the lines the patch included. Pierre asks for
// full contents the first time a viewer expands context, and "Add to Chat"
// needs them to slice a snippet, so both share one in-flight request per file.
const pending = new Map<string, Promise<ReviewFileContents>>()

function key(
  owner: string,
  repo: string,
  prNumber: number,
  path: string
): string {
  return `${owner}/${repo}#${prNumber}:${path}`
}

export function loadReviewFileContents(
  owner: string,
  repo: string,
  prNumber: number,
  file: ReviewDiffFile
): Promise<ReviewFileContents> {
  const cacheKey = key(
    owner,
    repo,
    prNumber,
    `${file.baseSha}:${file.headSha}:${file.previousPath}:${file.path}`
  )
  const cached = pending.get(cacheKey)
  if (cached) return cached
  const request = api
    .getReviewFileContents(
      owner,
      repo,
      prNumber,
      file.path,
      file.previousPath ?? file.path,
      file.baseSha,
      file.headSha
    )
    .then((contents) => {
      // An added file has no old side and a deleted one no new side; anything else missing is a failure.
      if (
        (contents.originalContent === null && file.status !== "added") ||
        (contents.modifiedContent === null && file.status !== "removed")
      )
        throw new Error("File contents are unavailable for this revision")
      return contents
    })
    .catch((error: unknown) => {
      pending.delete(cacheKey)
      throw error
    })
  pending.set(cacheKey, request)
  return request
}
