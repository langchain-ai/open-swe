export interface ScrollPosition {
  top: number
  /** Pinned to the live tail; restoring means following new content, not a fixed offset. */
  atBottom: boolean
}

const MAX_ENTRIES = 200
const positions = new Map<string, ScrollPosition>()

/** Where the user last was in a transcript, kept for the life of the page. */
export function readScrollPosition(key: string): ScrollPosition | undefined {
  return positions.get(key)
}

export function rememberScrollPosition(
  key: string,
  position: ScrollPosition
): void {
  positions.delete(key)
  positions.set(key, position)
  if (positions.size > MAX_ENTRIES) {
    const oldest = positions.keys().next().value
    if (oldest !== undefined) positions.delete(oldest)
  }
}
