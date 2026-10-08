import { createContext, useContext } from "react"

import type { DiffEntry } from "./diffEntries"

/** The rendered entries by item id, for the header and note slots CodeView portals into. */
export const EntriesContext = createContext<ReadonlyMap<string, DiffEntry>>(
  new Map()
)

export function useEntry(id: string): DiffEntry | undefined {
  return useContext(EntriesContext).get(id)
}

export const FILE_HEADER_HEIGHT = 40
