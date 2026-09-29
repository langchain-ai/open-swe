import * as z from "zod"

import { isRecord } from "./json.ts"

export const cliToolSchema = z.object({
  name: z.string(),
  description: z.string(),
  parameters: z.record(z.string(), z.json()),
  access: z.enum(["session", "admin"]),
})

export type CliTool = z.infer<typeof cliToolSchema>

export function mcpInputSchema(tool: CliTool): z.ZodType {
  const schema = tool.parameters
  if (schema["type"] !== "object" || !isRecord(schema["properties"]))
    throw new Error(`Invalid schema for ${tool.name}`)
  return z.fromJSONSchema(schema)
}
