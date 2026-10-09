import { readFile } from "node:fs/promises"
import { isAbsolute } from "node:path"
import { gzipSync } from "node:zlib"

import { normalizeBackend, uploadSession } from "./api.ts"

/**
 * Fill the thread the `upload_session` MCP tool reserved with a local
 * transcript, sent verbatim as gzipped JSONL: gzip keeps a large session under
 * Vercel's 4.5 MB request cap. The upload code is the only credential.
 */
export async function uploadTranscript(
  backend: string,
  code: string,
  transcriptPath: string
): Promise<string> {
  if (!isAbsolute(transcriptPath))
    throw new Error("the transcript path must be absolute")
  const transcript = await readFile(transcriptPath)
  return await uploadSession(
    normalizeBackend(backend),
    code,
    gzipSync(transcript)
  )
}
