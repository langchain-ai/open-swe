/** An `eval` call's output, split out of the interpreter's `<stdout>`/`<result>`/`<error>` wire format. */
export interface EvalOutput {
  stdout: string | null
  result: string | null
  error: { type: string; message: string } | null
}

const EVAL_OUTPUT =
  /^(?:<stdout>\n([\s\S]*?)\n<\/stdout>\n?)?(?:<error type="([^"]*)">([\s\S]*)<\/error>|<result(?: kind="[^"]*")?>([\s\S]*)<\/result>)$/

function unescapeXml(text: string): string {
  return text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&")
}

/** Null when the output is not in the interpreter's format, so it shows as is. */
export function parseEvalOutput(raw: string): EvalOutput | null {
  const match = EVAL_OUTPUT.exec(raw.trim())
  if (!match) return null
  const [, stdout, errorType, errorMessage, result] = match
  return {
    stdout: stdout ?? null,
    result: result === undefined ? null : unescapeXml(result),
    error:
      errorType === undefined
        ? null
        : {
            type: unescapeXml(errorType),
            message: unescapeXml(errorMessage ?? ""),
          },
  }
}
