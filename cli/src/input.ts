import { fstatSync } from "node:fs"

import { errorCode } from "./json.ts"

/**
 * Whether stdin is a pipe or a redirected file. A terminal, `/dev/null`, or a
 * handle a CI runner leaves open without ever closing is not, and reading one
 * of those would block or return nothing.
 */
function stdinIsPiped(): boolean {
  try {
    const stat = fstatSync(0)
    return stat.isFIFO() || stat.isFile()
  } catch (cause) {
    if (errorCode(cause) === "EBADF") return false
    throw cause
  }
}

export async function readPipedStdin(): Promise<string> {
  if (!stdinIsPiped()) return ""
  const chunks: Buffer[] = []
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk))
  return Buffer.concat(chunks).toString("utf8")
}

/** The instruction, with piped input attached below it; either alone is the prompt. */
export function composePrompt(instruction: string, piped: string): string {
  const task = instruction.trim()
  const input = piped.replace(/\n+$/, "")
  if (!input.trim()) return task
  if (!task) return input
  return `${task}\n\n<stdin>\n${input}\n</stdin>`
}
