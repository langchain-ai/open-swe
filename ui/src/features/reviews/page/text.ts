/** The first line a person would read, without Markdown's marks. */
export function plainFirstLine(body: string): string {
  for (const line of body.split("\n")) {
    const text = line
      .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/<[^>]+>/g, "")
      .replace(/[*_`#>~|]/g, "")
      .replace(/^\s*[-+]\s+/, "")
      .trim()
    if (text) return text
  }
  return ""
}
