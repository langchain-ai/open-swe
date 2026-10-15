/** The `eval` tool's wire format, split into its console output and its result or error. */
export interface EvalOutput {
  stdout: string | null
  result: { kind: string | null; value: string } | null
  error: { type: string; message: string } | null
}

const RESULT_TAIL = /(?:^|\n)<result(?: kind="([^"]*)")?>([^<]*)<\/result>$/
const ERROR_TAIL = /(?:^|\n)<error type="([^"]*)">([^<]*)<\/error>$/
const STDOUT_BLOCK = /^<stdout>\n([\s\S]*)\n<\/stdout>$/

function unescapeXml(text: string): string {
  return text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&")
}

export function parseEvalOutput(raw: string): EvalOutput | null {
  const text = raw.trim()
  const result = RESULT_TAIL.exec(text)
  const error = result ? null : ERROR_TAIL.exec(text)
  const tail = result ?? error
  if (!tail) return null

  const head = text.slice(0, tail.index)
  const stdout = head ? STDOUT_BLOCK.exec(head) : null
  if (head && !stdout) return null

  return {
    stdout: stdout?.[1] ?? null,
    result: result
      ? { kind: result[1] ?? null, value: unescapeXml(result[2] ?? "") }
      : null,
    error: error
      ? {
          type: unescapeXml(error[1] ?? ""),
          message: unescapeXml(error[2] ?? ""),
        }
      : null,
  }
}
