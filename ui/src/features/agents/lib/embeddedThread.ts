import { createContext } from "react"
import type { ReactNode } from "react"

/**
 * A thread shown inside another page, such as the review page's rail or the
 * dock beside a plugin: that page is its header, so the thread view leaves out
 * its own header, pull request strip and side panels.
 */
export const EmbeddedThreadContext = createContext<{
  /** What an empty thread shows: ways in, specific to the page around it. */
  emptyState?: ReactNode
  /** Run config keys for every message sent from here, which the backend reads by name. */
  runConfigurable?: Readonly<Record<string, boolean | string>>
} | null>(null)
