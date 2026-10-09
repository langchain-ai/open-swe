import * as z from "zod"

const PULL_REQUEST_URL =
  /^https:\/\/github\.com\/([^/\s]+)\/([^/\s]+)\/pull\/(\d+)\/?$/

const prUrl = z
  .string()
  .regex(
    PULL_REQUEST_URL,
    "pr_url must be https://github.com/owner/repo/pull/N"
  )
  .describe("The GitHub pull request, as https://github.com/owner/repo/pull/N")

export const requestHumanReviewArgs = {
  pr_url: prUrl,
  inline_summary: z
    .string()
    .trim()
    .min(1)
    .max(280)
    .describe(
      "One or two plain sentences on what the change does and why, as a reviewer wants to know before opening it. Write it from the pull request itself: what you wrote when you opened it, or its description and diff read now; never from the branch name or the title alone. No pull request numbers, URLs, SHAs, file paths or names"
    ),
  channel: z
    .string()
    .optional()
    .describe(
      "Slack channel name like #eng-reviews or a channel id; defaults to the repository's reviewChannel in .open-swe/settings.json"
    ),
}

export const requestHumanReviewResult = {
  request_id: z.string(),
  channel: z.string(),
  reused: z.boolean(),
  summary_updated: z.boolean(),
}

export const dismissHumanReviewRequestArgs = {
  pr_url: prUrl,
  reason: z
    .string()
    .optional()
    .describe("A few words shown on the dismissed card"),
}

export const dismissHumanReviewRequestResult = {
  request_id: z.string(),
}

export const humanReviewRequestSchema = z.object(requestHumanReviewResult)
export const humanReviewDismissSchema = z.object(
  dismissHumanReviewRequestResult
)

export type HumanReviewRequestResult = z.infer<typeof humanReviewRequestSchema>
export type HumanReviewDismissResult = z.infer<typeof humanReviewDismissSchema>

/** The web app API path of a pull request URL the schema already checked. */
export function pullRequestApiPath(url: string): string {
  const match = PULL_REQUEST_URL.exec(url)
  if (match === null) throw new Error(`not a pull request URL: ${url}`)
  const [, owner = "", repo = "", number = ""] = match
  return `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/pulls/${number}`
}
