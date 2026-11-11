import type { Href } from "expo-router"

import { CATEGORY_TITLES, type InboxCategory } from "../core/models"

/** Which agents a list shows. */
export type AgentListScope =
  | { scope: "all" }
  | { scope: "search" }
  | { scope: "category"; category: InboxCategory }
  | { scope: "workspace"; key: string; name: string }

export function listHref(scope: AgentListScope): Href {
  return { pathname: "/list", params: scope }
}

/** The scope a list route's params describe. */
export function parseListScope(
  params: Record<string, string | string[] | undefined>
): AgentListScope {
  const value = (key: string) => {
    const raw = params[key]
    return Array.isArray(raw) ? raw[0] : raw
  }
  switch (value("scope")) {
    case "search":
      return { scope: "search" }
    case "category":
      return {
        scope: "category",
        category: (value("category") as InboxCategory) ?? "needsAttention",
      }
    case "workspace":
      return {
        scope: "workspace",
        key: value("key") ?? "",
        name: value("name") ?? "No Repo",
      }
    default:
      return { scope: "all" }
  }
}

export function scopeTitle(scope: AgentListScope): string {
  switch (scope.scope) {
    case "all":
    case "search":
      return "All Agents"
    case "category":
      return CATEGORY_TITLES[scope.category]
    case "workspace":
      return scope.name
  }
}

export const threadHref = (id: string): Href => ({
  pathname: "/thread/[id]",
  params: { id },
})
export const changesHref = (id: string): Href => ({
  pathname: "/changes/[id]",
  params: { id },
})
export const detailsHref = (id: string): Href => ({
  pathname: "/details/[id]",
  params: { id },
})
