import { readFile } from "node:fs/promises"
import { isAbsolute } from "node:path"
import { gzipSync } from "node:zlib"

import * as z from "zod"

export const SESSION_TYPES = ["claude"] as const

export type SessionType = (typeof SESSION_TYPES)[number]

export const uploadSessionArgs = {
  type: z
    .enum(SESSION_TYPES)
    .describe("Which coding agent wrote the transcript"),
  transcript_path: z
    .string()
    .refine(isAbsolute, "transcript_path must be an absolute path")
    .describe(
      "Absolute path of the session's JSONL transcript. For Claude Code it is ~/.claude/projects/<the session's working directory with every character other than a letter or digit replaced by '-'>/<session id>.jsonl, where the session id is $CLAUDE_CODE_SESSION_ID"
    ),
  repo: z
    .string()
    .regex(/^[^/\s]+\/[^/\s]+$/, "repo must be owner/name")
    .optional()
    .describe(
      "GitHub repository (owner/name) the working directory was pushed to; pass with branch, or pass pr_url instead"
    ),
  branch: z
    .string()
    .min(1)
    .optional()
    .describe(
      "Branch on repo holding the whole working directory, committed and pushed"
    ),
  pr_url: z
    .url()
    .optional()
    .describe(
      "Pull request whose head branch holds the whole working directory, committed and pushed; replaces repo and branch"
    ),
  visibility: z
    .enum(["workspace", "private"])
    .default("workspace")
    .describe("Who can see the new thread"),
}

export const uploadSessionResult = {
  thread_id: z.string(),
  url: z.string(),
}

export const uploadedThreadSchema = z.object({ id: z.string() })

type UploadSessionArgs = z.infer<z.ZodObject<typeof uploadSessionArgs>>

export interface SessionUploadHeader {
  type: SessionType
  repo?: string
  branch?: string
  pr_url?: string
  visibility: "workspace" | "private"
}

/**
 * The request body as gzipped JSONL: the header line, then the transcript
 * verbatim. Gzip keeps a large session under Vercel's 4.5 MB request cap.
 */
export async function sessionUpload(
  args: UploadSessionArgs
): Promise<Uint8Array> {
  const target =
    args.pr_url !== undefined
      ? args.repo === undefined && args.branch === undefined
        ? { pr_url: args.pr_url }
        : null
      : args.repo !== undefined && args.branch !== undefined
        ? { repo: args.repo, branch: args.branch }
        : null
  if (target === null)
    throw new Error("pass repo and branch, or pr_url on its own")
  const header: SessionUploadHeader = {
    type: args.type,
    visibility: args.visibility,
    ...target,
  }
  return gzipSync(
    Buffer.concat([
      Buffer.from(`${JSON.stringify(header)}\n`),
      await readFile(args.transcript_path),
    ])
  )
}
