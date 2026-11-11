import type { DiffFile } from "./models"

/** One rendered row of a file diff. */
export type DiffLine =
  | { kind: "gap"; count: number }
  | { kind: "context"; old: number; new: number; text: string }
  | { kind: "added"; new: number; text: string }
  | { kind: "removed"; old: number; text: string }

/** The rows to render: from the unified patch when there is one, else from both sides' contents. */
export function diffLines(file: DiffFile): DiffLine[] {
  if (file.patch) return linesFromPatch(file.patch)
  if (
    file.originalContent !== undefined ||
    file.modifiedContent !== undefined
  ) {
    return linesFromContents(
      file.originalContent ?? "",
      file.modifiedContent ?? ""
    )
  }
  return []
}

export function linesFromPatch(patch: string): DiffLine[] {
  const result: DiffLine[] = []
  let old = 0
  let next = 0
  let shownThrough = 0
  for (const raw of patch.split("\n")) {
    if (raw.startsWith("@@")) {
      const [oldStart, newStart] = hunkStarts(raw)
      old = oldStart
      next = newStart
      const hidden = newStart - 1 - shownThrough
      if (hidden > 0) result.push({ kind: "gap", count: hidden })
      continue
    }
    // Before the first hunk come the file headers (`diff`, `index`, `---`,
    // `+++`). Inside one, every `+` or `-` line is a change, even one whose
    // text makes it start `+++` or `---`.
    if (old === 0 && next === 0) continue
    if (raw.startsWith("\\")) continue
    const text = raw.slice(1)
    if (raw.startsWith("+")) {
      result.push({ kind: "added", new: next, text })
      shownThrough = next
      next += 1
    } else if (raw.startsWith("-")) {
      result.push({ kind: "removed", old, text })
      old += 1
    } else {
      result.push({ kind: "context", old, new: next, text })
      shownThrough = next
      old += 1
      next += 1
    }
  }
  while (true) {
    const last = result[result.length - 1]
    if (last?.kind === "context" && last.text === "") result.pop()
    else break
  }
  return result
}

/** "@@ -17,5 +17,5 @@ def …" */
function hunkStarts(header: string): [number, number] {
  const parts = header.split(" ")
  const start = (prefix: string) => {
    const token = parts.find((part) => part.startsWith(prefix))
    const value = Number.parseInt(token?.slice(1).split(",")[0] ?? "1", 10)
    return Number.isNaN(value) ? 1 : value
  }
  return [start("-"), start("+")]
}

/** Diff two full texts and keep three lines of context around each change. */
export function linesFromContents(
  oldText: string,
  newText: string,
  context = 3
): DiffLine[] {
  const all = diffAll(oldText.split("\n"), newText.split("\n"))
  const keep = new Set<number>()
  all.forEach((line, index) => {
    if (line.kind === "context") return
    for (
      let i = Math.max(0, index - context);
      i <= Math.min(all.length - 1, index + context);
      i++
    )
      keep.add(i)
  })
  const result: DiffLine[] = []
  let hidden = 0
  all.forEach((line, index) => {
    if (keep.has(index)) {
      if (hidden > 0) result.push({ kind: "gap", count: hidden })
      hidden = 0
      result.push(line)
    } else {
      hidden += 1
    }
  })
  if (hidden > 0 && result.length > 0)
    result.push({ kind: "gap", count: hidden })
  return result
}

/** Every line of both texts in order, from a Myers shortest edit script. */
function diffAll(a: string[], b: string[]): DiffLine[] {
  const max = a.length + b.length
  const offset = max + 1
  let v = Array.from({ length: 2 * max + 3 }, () => 0)
  const trace: number[][] = []
  outer: for (let d = 0; d <= max; d++) {
    trace.push(v.slice())
    for (let k = -d; k <= d; k += 2) {
      let x =
        k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1])
          ? v[offset + k + 1]
          : v[offset + k - 1] + 1
      let y = x - k
      while (x < a.length && y < b.length && a[x] === b[y]) {
        x++
        y++
      }
      v[offset + k] = x
      if (x >= a.length && y >= b.length) {
        trace.push(v.slice())
        break outer
      }
    }
  }
  // Walk the trace back from the end, emitting lines in reverse.
  const lines: DiffLine[] = []
  let x = a.length
  let y = b.length
  for (let d = trace.length - 2; d >= 0; d--) {
    v = trace[d]
    const k = x - y
    const prevK =
      k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1])
        ? k + 1
        : k - 1
    const prevX = v[offset + prevK]
    const prevY = prevX - prevK
    while (x > prevX && y > prevY) {
      lines.push({ kind: "context", old: x, new: y, text: b[y - 1] })
      x--
      y--
    }
    if (d === 0) break
    if (x === prevX) lines.push({ kind: "added", new: y, text: b[y - 1] })
    else lines.push({ kind: "removed", old: x, text: a[x - 1] })
    x = prevX
    y = prevY
  }
  while (x > 0 && y > 0) {
    lines.push({ kind: "context", old: x, new: y, text: b[y - 1] })
    x--
    y--
  }
  return lines.reverse()
}
