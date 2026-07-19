export function humanizeToolName(name: string, fallback = "Tool"): string {
  const normalized = name
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase()

  if (!normalized) return fallback
  return `${normalized.charAt(0).toUpperCase()}${normalized.slice(1)}`
}
