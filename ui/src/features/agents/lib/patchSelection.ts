export type PatchSide = "additions" | "deletions"

export interface PatchFile {
  /** Path on the selected side; the new path for edits and additions. */
  path: string
  patch: string
}

const HUNK_HEADER = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/

/**
 * How many old- and new-side lines a hunk header declares. An omitted count
 * means one line, per the unified-diff format.
 */
function hunkExtent(header: RegExpExecArray): { old: number; new: number } {
  return {
    old: header[2] === undefined ? 1 : Number(header[2]),
    new: header[4] === undefined ? 1 : Number(header[4]),
  }
}

/** Split a unified patch into one entry per file, keeping each file's raw text. */
export function splitPatch(patch: string): Array<PatchFile> {
  const lines = patch.split("\n")
  const files: Array<PatchFile> = []
  let current: Array<string> | null = null
  const flush = () => {
    if (!current) return
    const text = current.join("\n")
    files.push({ path: patchPath(current), patch: text })
    current = null
  }
  // Consume each hunk for exactly the number of lines its header declares. A
  // deleted source line beginning `-- ` renders as `--- foo`, indistinguishable
  // from a file header by prefix alone; only position inside a hunk settles it.
  let remainingOld = 0
  let remainingNew = 0

  lines.forEach((line, index) => {
    const inHunk = remainingOld > 0 || remainingNew > 0
    if (!inHunk) {
      const startsFile =
        line.startsWith("diff --git ") ||
        (line.startsWith("--- ") &&
          (lines[index + 1]?.startsWith("+++ ") ?? false) &&
          current !== null &&
          current.some((l) => HUNK_HEADER.test(l)))
      if (startsFile) flush()
    }
    if (!current) current = []
    current.push(line)

    const header = HUNK_HEADER.exec(line)
    if (header && !inHunk) {
      const extent = hunkExtent(header)
      remainingOld = extent.old
      remainingNew = extent.new
      return
    }
    if (!inHunk || line.startsWith("\\")) return
    if (line.startsWith("+")) remainingNew -= 1
    else if (line.startsWith("-")) remainingOld -= 1
    else {
      remainingOld -= 1
      remainingNew -= 1
    }
  })
  flush()
  return files.filter((file) => file.patch.trim().length > 0)
}

function patchPath(lines: Array<string>): string {
  const plus = lines.find((line) => line.startsWith("+++ "))
  const minus = lines.find((line) => line.startsWith("--- "))
  const candidate =
    plus && !plus.startsWith("+++ /dev/null") ? plus : (minus ?? plus)
  if (candidate) {
    const raw = candidate.slice(4).trim().split("\t")[0] ?? ""
    return raw.replace(/^[ab]\//, "")
  }
  const git = lines.find((line) => line.startsWith("diff --git "))
  if (git) {
    const match = /^diff --git a\/(.+?) b\/(.+)$/.exec(git)
    if (match) return match[2] ?? match[1] ?? ""
  }
  return ""
}

/**
 * Lines of a single-file patch whose line number on `side` falls in
 * [start, end], with their `+`/`-`/space prefixes kept.
 */
export function selectPatchLines(
  patch: string,
  side: PatchSide,
  start: number,
  end: number
): Array<string> {
  const selected: Array<string> = []
  let oldLine = 0
  let newLine = 0
  let inHunk = false
  for (const line of patch.split("\n")) {
    const header = HUNK_HEADER.exec(line)
    if (header) {
      oldLine = Number(header[1])
      newLine = Number(header[3])
      inHunk = true
      continue
    }
    if (!inHunk) continue
    if (line.startsWith("\\")) continue
    const marker = line[0]
    if (marker === "+") {
      if (side === "additions" && newLine >= start && newLine <= end) {
        selected.push(line)
      }
      newLine += 1
    } else if (marker === "-") {
      if (side === "deletions" && oldLine >= start && oldLine <= end) {
        selected.push(line)
      }
      oldLine += 1
    } else if (marker === " " || line === "") {
      const number = side === "additions" ? newLine : oldLine
      if (number >= start && number <= end) selected.push(line || " ")
      oldLine += 1
      newLine += 1
    } else {
      inHunk = false
    }
  }
  return selected
}
