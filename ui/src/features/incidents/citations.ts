type Source = { id: string; url?: string | null }

export function citationPreview(text: string) {
  return text
    .replace(
      /\[(?:[A-Za-z][\w-]*:[\w.:-]+)(?:,\s*[A-Za-z][\w-]*:[\w.:-]+)*\]/g,
      ""
    )
    .replace(/\s+([.,;:])/g, "$1")
    .replace(/\s+/g, " ")
    .trim()
}

export function citationIndices(part: string, evidence: Source[]) {
  if (!part.startsWith("[") || !part.endsWith("]")) return []
  const indices = part
    .slice(1, -1)
    .split(",")
    .map((id) => evidence.findIndex((item) => item.id === id.trim()))
  return indices.every((index) => index >= 0) ? [...new Set(indices)] : []
}
