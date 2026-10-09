import * as z from "zod"

export const createSessionArgs = {
  prompt: z
    .string()
    .trim()
    .min(1)
    .describe("The task for the new agent session."),
  repo: z
    .string()
    .regex(/^[^/\s]+\/[^/\s]+$/)
    .optional()
    .describe("GitHub owner/repo; defaults to your saved repository."),
  workspace: z
    .string()
    .optional()
    .describe("Workspace slug; defaults to web workspace routing."),
  visibility: z
    .enum(["public", "private"])
    .optional()
    .describe("Defaults to your saved web visibility."),
  start: z
    .boolean()
    .default(true)
    .describe("Start the agent immediately; false creates an idle session."),
}

export type CreateSessionArgs = z.infer<z.ZodObject<typeof createSessionArgs>>

export const createdSessionSchema = z.object({ thread_id: z.string().min(1) })
export const createSessionResult = {
  thread_id: z.string(),
  url: z.string(),
}
