/** A scroll position as an offset from the top of one file's row, so it survives content above it changing. */
export interface ScrollAnchor {
  path: string
  offset: number
}

const anchorPattern = /^(-?\d+)@(.+)$/

export function parseScrollAnchor(
  value: string | undefined
): ScrollAnchor | null {
  const match = value ? anchorPattern.exec(value) : null
  return match ? { offset: Number(match[1]), path: match[2]! } : null
}
