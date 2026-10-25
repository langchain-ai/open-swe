import type { PluginHost, SharedRepository } from "@openswe/plugin-sdk"

/**
 * What Docs keeps. Pages are the repository's Markdown files; a draft is a
 * branch whose live state is a shared Yjs document until someone saves a
 * version. Drafts and comments are plugin documents shared with everyone who
 * can read the repository.
 */

/** A page's open edit: a branch off the default one, published as a pull request. */
export interface Draft {
  branch: string
  base: string
  /** The last commit Docs saved on the branch; another head means someone pushed. */
  head: string
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

/** Drafts by the path of the page they edit. */
export function draftsIn(host: PluginHost, repository: string) {
  return host.storage.collection<string, Draft>("drafts", { repository })
}

/** Comments and suggestions on the repository's pages, by comment id. */
export function commentsIn(host: PluginHost, repository: string) {
  return host.storage.collection<string, PageComment>("comments", {
    repository,
  })
}

/** A repository's name alone when it is in one of this Open SWE's organizations, else `owner/repo`. */
export function repositoryLabel(repository: SharedRepository): string {
  return repository.isInBaseOrg
    ? (repository.fullName.split("/")[1] ?? repository.fullName)
    : repository.fullName
}

export function isMarkdown(path: string): boolean {
  return /\.(md|markdown)$/i.test(path)
}

/** Dotfiles and anything under a dot-directory: tooling, not pages. */
export function isHidden(path: string): boolean {
  return path.split("/").some((segment) => segment.startsWith("."))
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
  return `docs/${slug || "page"}-${Date.now().toString(36)}`
}

export function draftRoom(draft: Draft, path: string): string {
  return `draft/${draft.branch}/${path}`
}

export function presenceRoom(path: string): string {
  return `presence/${path}`
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

const COLORS = [
  "#2563eb",
  "#db2777",
  "#16a34a",
  "#ea580c",
  "#7c3aed",
  "#0891b2",
  "#ca8a04",
  "#dc2626",
]

/** A stable colour per person, for their cursor and avatar. */
export function colorFor(login: string): string {
  let hash = 0
  for (const char of login) hash = (hash * 31 + char.charCodeAt(0)) | 0
  return COLORS[Math.abs(hash) % COLORS.length] ?? "#2563eb"
}

/** Where a link in a page goes: another page here, GitHub, or elsewhere. */
export function resolveLink(
  href: string,
  fromPath: string
): { page: string } | { url: string } | null {
  if (/^https?:\/\//i.test(href)) return { url: href }
  if (href.startsWith("#") || href.startsWith("mailto:")) return null
  const [target = ""] = href.split("#")
  const base = fromPath.split("/").slice(0, -1)
  const parts = target.startsWith("/") ? [] : base
  for (const segment of target.split("/")) {
    if (!segment || segment === ".") continue
    if (segment === "..") parts.pop()
    else parts.push(decodeURIComponent(segment))
  }
  return parts.length ? { page: parts.join("/") } : null
}
