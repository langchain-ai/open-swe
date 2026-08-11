export interface TerminalLinkMatch {
  readonly text: string
  readonly start: number
  readonly end: number
}

export interface TerminalBufferLineLike {
  readonly isWrapped?: boolean
  translateToString: (trimRight?: boolean) => string
}

export interface WrappedTerminalLinkLineSegment {
  readonly bufferLineNumber: number
  readonly startIndex: number
  readonly endIndex: number
}

export interface WrappedTerminalLinkLine {
  readonly text: string
  readonly segments: ReadonlyArray<WrappedTerminalLinkLineSegment>
}

const URL_PATTERN = /https?:\/\/[^\s"'`<>]+/g
const FILE_PATH_PATTERN =
  /(?:~\/|\.{1,2}\/|\/|[A-Za-z]:[\\/]|\\\\)[^\s"'`<>]+|[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)+(?::\d+){0,2}/g
const TRAILING_PUNCTUATION_PATTERN = /[.,;!?]+$/

function trimClosingDelimiters(value: string): string {
  let output = value.replace(TRAILING_PUNCTUATION_PATTERN, "")
  for (const [open, close] of [
    ["(", ")"],
    ["[", "]"],
    ["{", "}"],
  ] as const) {
    while (
      output.endsWith(close) &&
      output.split(open).length < output.split(close).length
    ) {
      output = output.slice(0, -1)
    }
  }
  return output
}

export function extractTerminalLinks(line: string): Array<TerminalLinkMatch> {
  const matches: Array<TerminalLinkMatch> = []
  for (const pattern of [URL_PATTERN, FILE_PATH_PATTERN]) {
    pattern.lastIndex = 0
    for (const match of line.matchAll(pattern)) {
      const start = match.index
      const text = trimClosingDelimiters(match[0])
      if (
        !text ||
        matches.some(
          (item) => start < item.end && item.start < start + text.length
        )
      )
        continue
      matches.push({ text, start, end: start + text.length })
    }
  }
  return matches.sort((left, right) => left.start - right.start)
}

export function collectWrappedTerminalLinkLine(
  bufferLineNumber: number,
  getLine: (
    bufferLineIndex: number
  ) => TerminalBufferLineLike | null | undefined
): WrappedTerminalLinkLine | null {
  let current = bufferLineNumber
  let line = getLine(current - 1)
  if (!line) return null
  while (current > 1 && line.isWrapped) {
    line = getLine(current - 2)
    if (!line) return null
    current -= 1
  }

  const segments: Array<WrappedTerminalLinkLineSegment> = []
  let offset = 0
  for (;;) {
    line = getLine(current - 1)
    if (!line) break
    const continues = getLine(current)?.isWrapped === true
    const text = line.translateToString(!continues)
    segments.push({
      bufferLineNumber: current,
      startIndex: offset,
      endIndex: offset + text.length,
    })
    offset += text.length
    if (!continues) break
    current += 1
  }
  return {
    text: segments
      .map((segment, index) => {
        const next = segments[index + 1]
        return (
          getLine(segment.bufferLineNumber - 1)?.translateToString(
            next !== undefined ? false : true
          ) ?? ""
        )
      })
      .join(""),
    segments,
  }
}
