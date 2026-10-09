import { createContext, useContext } from "react"

import type { DiffEntry } from "./diffEntries"
import type { FileMarkers } from "./queries"

/** Per-file findings and threads, computed once for every file header CodeView portals in. */
export const MarkersContext = createContext<ReadonlyMap<string, FileMarkers>>(
  new Map()
)

const NO_MARKERS: FileMarkers = { findings: [], threads: [] }

export function useMarkers(path: string): FileMarkers {
  return useContext(MarkersContext).get(path) ?? NO_MARKERS
}

/** The rendered entries by item id, for the header and note slots CodeView portals into. */
export const EntriesContext = createContext<ReadonlyMap<string, DiffEntry>>(
  new Map()
)

export function useEntry(id: string): DiffEntry | undefined {
  return useContext(EntriesContext).get(id)
}
