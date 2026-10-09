/** The first line a person would read, without Markdown's marks. */
export function plainFirstLine(body: string): string {
  // Parsed rather than regex-stripped, which nested tags like `<scr<script>ipt>` survive.
  const html = new DOMParser().parseFromString(body, "text/html")
  for (const line of (html.body.textContent ?? "").split("\n")) {
    const text = line
      .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/[*_`#>~|]/g, "")
      .replace(/^\s*[-+]\s+/, "")
      .trim()
    if (text) return text
  }
  return ""
}

/** "1 file", "3 files"; pass `many` for irregular plurals. */
export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`
}

/** "routes.py:53", or just the path without a line. */
export function withLine(path: string, line: number | null): string {
  return line ? `${path}:${line}` : path
}

/** A repository path as its folder (with trailing slash) and file name. */
export function splitPath(path: string): { dir: string; name: string } {
  const slash = path.lastIndexOf("/")
  return { dir: path.slice(0, slash + 1), name: path.slice(slash + 1) }
}
