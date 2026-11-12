import type { FrontendReviewRun } from "./capture"

/** `path` on the site at `base`, or nothing when the result would leave that site. */
export function pageLink(base: string, path: string): string | undefined {
  try {
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\"))
      return
    const origin = new URL(base)
    const url = new URL(`${base.replace(/\/$/, "")}${path}`)
    const local =
      origin.protocol === "http:" &&
      ["127.0.0.1", "localhost"].includes(origin.hostname)
    if (
      (!local && origin.protocol !== "https:") ||
      origin.username ||
      origin.password ||
      origin.search ||
      origin.hash ||
      url.origin !== origin.origin
    )
      return
    return url.href
  } catch {
    return
  }
}

export interface PageLink {
  label: "Production" | "Preview" | "Base"
  href: string
}

/** A scenario's page on each site the run knows, without repeating a link. */
export function scenarioLinks(
  run: FrontendReviewRun,
  path: string
): PageLink[] {
  const origins = [
    ["Production", run.report?.production_url],
    ["Preview", run.head_url],
    ["Base", run.base_url],
  ] as const
  const links: PageLink[] = []
  for (const [label, origin] of origins) {
    const href = origin ? pageLink(origin, path) : undefined
    if (href && !links.some((link) => link.href === href))
      links.push({ label, href })
  }
  return links
}
