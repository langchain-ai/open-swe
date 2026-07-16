const TOOL_NAME_WORDS: Record<string, string> = {
  api: "API",
  ci: "CI",
  cli: "CLI",
  github: "GitHub",
  http: "HTTP",
  id: "ID",
  ids: "IDs",
  oauth: "OAuth",
  pr: "PR",
  ui: "UI",
  uri: "URI",
  url: "URL",
  urls: "URLs",
}

function titleCaseWord(word: string): string {
  const lower = word.toLowerCase()
  return (
    TOOL_NAME_WORDS[lower] ??
    `${lower.charAt(0).toUpperCase()}${lower.slice(1)}`
  )
}

export function humanizeToolName(name: string, fallback = "Tool"): string {
  const words = name.replace(/[_-]+/g, " ").trim().split(/\s+/).filter(Boolean)

  if (!words.length) return fallback
  return words.map(titleCaseWord).join(" ")
}
