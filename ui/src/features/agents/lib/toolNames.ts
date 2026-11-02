export function humanizeToolName(name: string, fallback = "Tool"): string {
  const mcp =
    /^mcp_([^_]+)_(.+)$/i.exec(name.trim()) ??
    /^([^:]+): (.+)$/.exec(name.trim())
  if (mcp) {
    return mcp
      .slice(1)
      .map((part) =>
        part
          .replace(/[_-]+/g, " ")
          .trim()
          .toLowerCase()
          .replace(/\b\w/g, (letter) => letter.toUpperCase())
      )
      .join(": ")
  }

  const normalized = name
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase()

  if (!normalized) return fallback
  return `${normalized.charAt(0).toUpperCase()}${normalized.slice(1)}`
}
