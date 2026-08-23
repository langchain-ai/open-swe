const MAX_JSON_TOOL_RESULT_BYTES = 1024 * 1024

export function formatJsonToolResult(value: string): string | null {
  if (value[0] !== "{" && value[0] !== "[") return null
  if (
    new TextEncoder().encode(value).byteLength >= MAX_JSON_TOOL_RESULT_BYTES
  ) {
    return null
  }

  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return null
  }
}
