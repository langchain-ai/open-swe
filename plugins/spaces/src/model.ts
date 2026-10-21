/**
 * What Spaces keeps. Pages are Markdown files in the repository; everything
 * else is a plugin document shared with whoever can read that repository.
 */

/** A page's open edit: a branch off the default one, published as a pull request. */
export interface Draft {
  [key: string]: string
  branch: string
  base: string
  startedBy: string
  startedAt: string
}

/** Where in the page a comment points: the quoted text and what surrounds it. */
export interface Anchor {
  quote: string
  prefix: string
  suffix: string
}

export type CommentStatus = "open" | "resolved" | "accepted" | "rejected"

export interface PageComment {
  [key: string]: string | null
  path: string
  /** The comment this one replies to; replies carry no anchor of their own. */
  parent: string | null
  quote: string
  prefix: string
  suffix: string
  body: string
  /** With a suggestion, accepting replaces the quote with it. */
  suggestion: string | null
  author: string
  createdAt: string
  status: CommentStatus
}

export const DRAFTS = "drafts"
export const COMMENTS = "comments"

export function isMarkdown(path: string): boolean {
  return /\.(md|markdown)$/i.test(path)
}

export function pageTitle(path: string): string {
  const name = path.split("/").pop() ?? path
  return name.replace(/\.(md|markdown)$/i, "").replace(/[-_]+/g, " ")
}

export function draftBranch(path: string): string {
  const slug = path
    .replace(/\.(md|markdown)$/i, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40)
  return `spaces/${slug || "page"}-${Date.now().toString(36)}`
}

export function newId(): string {
  return crypto.randomUUID()
}

/** A page's YAML front matter, kept out of the editor and put back on save. */
export function splitFrontMatter(text: string): {
  frontMatter: string
  body: string
} {
  const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n/.exec(text)
  return match
    ? { frontMatter: match[0], body: text.slice(match[0].length) }
    : { frontMatter: "", body: text }
}

export function withTrailingNewline(text: string): string {
  return text.endsWith("\n") ? text : `${text}\n`
}

export function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  })
}
