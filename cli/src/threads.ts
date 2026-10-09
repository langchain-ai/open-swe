import * as z from "zod"

export const THREAD_STATUSES = [
  "idle",
  "running",
  "finished",
  "interrupted",
  "error",
] as const

export const THREAD_SOURCES = [
  "dashboard",
  "github",
  "slack",
  "linear",
  "schedule",
] as const

/** `list_threads` arguments: the web app sidebar's filters, plus the page's own. */
export const listThreadsArgs = {
  repo: z
    .string()
    .regex(/^[^/\s]+\/[^/\s]+$/, "repo must be owner/name")
    .optional()
    .describe(
      "Only threads in this repository (owner/name), like a sidebar repo group"
    ),
  no_repo: z
    .boolean()
    .default(false)
    .describe("Only chats with no repository, like the sidebar's Chats group"),
  include_archived: z
    .boolean()
    .default(false)
    .describe("Include archived (resolved) threads"),
  include_automations: z
    .boolean()
    .default(false)
    .describe("Include threads started by automations and schedules"),
  sort: z
    .enum(["created", "updated"])
    .default("created")
    .describe("Newest first by creation or by last activity"),
  status: z.enum(THREAD_STATUSES).optional(),
  unread: z
    .boolean()
    .optional()
    .describe("true for unread threads only, false for read ones only"),
  source: z
    .enum(THREAD_SOURCES)
    .optional()
    .describe("Where the thread was started"),
  query: z
    .string()
    .min(1)
    .optional()
    .describe("Matches title, repository, branch and pull request fields"),
  limit: z.number().int().min(1).max(100).default(25),
  offset: z.number().int().min(0).default(0),
}

export type ListThreadsArgs = z.infer<z.ZodObject<typeof listThreadsArgs>>

export function threadsPageQuery(args: ListThreadsArgs): URLSearchParams {
  if (args.repo !== undefined && args.no_repo)
    throw new Error("repo and no_repo are mutually exclusive")
  const search = new URLSearchParams({
    limit: String(args.limit),
    offset: String(args.offset),
    scope: args.include_automations ? "all" : "interactive",
    sort_by: args.sort === "created" ? "created_at" : "updated_at",
  })
  if (!args.include_archived) search.set("resolved", "false")
  if (args.repo !== undefined) search.set("repo", args.repo)
  if (args.no_repo) search.set("ownerless", "true")
  if (args.status !== undefined) search.set("status", args.status)
  if (args.unread !== undefined) search.set("viewed", String(!args.unread))
  if (args.source !== undefined) search.set("source", args.source)
  if (args.query !== undefined) search.set("q", args.query)
  return search
}

const pullRequestSchema = z.object({
  number: z.number().nullish(),
  title: z.string().nullish(),
  state: z.string().nullish(),
  url: z.string().nullish(),
})

const threadSummarySchema = z.object({
  id: z.string(),
  title: z.string().nullish(),
  status: z.string().nullish(),
  repoFullName: z.string().nullish(),
  branch: z.string().nullish(),
  source: z.string().nullish(),
  automationName: z.string().nullish(),
  attentionReason: z.string().nullish(),
  viewed: z.boolean().nullish(),
  resolved: z.boolean().nullish(),
  createdAt: z.number().nullish(),
  updatedAt: z.number().nullish(),
  pr: pullRequestSchema.nullish(),
})

export type ThreadSummary = z.infer<typeof threadSummarySchema>

export const threadsPageSchema = z.object({
  items: z.array(threadSummarySchema),
  offset: z.number(),
  hasMore: z.boolean().nullish(),
})

export type ThreadsPage = z.infer<typeof threadsPageSchema>

export const identitySchema = z.object({
  login: z.string(),
  email: z.string().nullish(),
  is_admin: z.boolean().nullish(),
})

export type Identity = z.infer<typeof identitySchema>

const listedThreadSchema = z.object({
  id: z.string(),
  title: z.string(),
  status: z.string(),
  unread: z.boolean(),
  archived: z.boolean(),
  repo: z.string().nullable(),
  branch: z.string().nullable(),
  source: z.string().nullable(),
  automation: z.string().nullable(),
  attention: z.string().nullable(),
  pull_request: pullRequestSchema.nullable(),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
  url: z.string(),
})

export const listThreadsResult = {
  threads: z.array(listedThreadSchema),
  has_more: z.boolean(),
  next_offset: z.number().nullable(),
}

export type ListThreadsResult = z.infer<z.ZodObject<typeof listThreadsResult>>

function isoTime(ms: number | null | undefined): string | null {
  return ms == null ? null : new Date(ms).toISOString()
}

function present(value: string | null | undefined): string | null {
  return value == null || value.trim() === "" ? null : value
}

export function listThreadsResultFrom(
  page: ThreadsPage,
  threadUrl: (id: string) => string
): ListThreadsResult {
  const hasMore = page.hasMore === true
  return {
    threads: page.items.map((thread) => ({
      id: thread.id,
      title: thread.title ?? "",
      status: thread.status ?? "idle",
      unread: thread.viewed === false,
      archived: thread.resolved === true,
      repo: present(thread.repoFullName),
      branch: present(thread.branch),
      source: present(thread.source),
      automation: present(thread.automationName),
      attention: present(thread.attentionReason),
      pull_request: thread.pr ?? null,
      created_at: isoTime(thread.createdAt),
      updated_at: isoTime(thread.updatedAt),
      url: threadUrl(thread.id),
    })),
    has_more: hasMore,
    next_offset: hasMore ? page.offset + page.items.length : null,
  }
}
