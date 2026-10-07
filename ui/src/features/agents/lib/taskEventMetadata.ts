import { Validator } from "@langchain/core/utils/json_schema"

import schema from "./taskEvent.schema.json"

interface TaskEventSource {
  version: 1
  task_id: string
  sender_thread_id: string
  sender_role: "worker" | "coordinator"
  sender_label: string | null
  content: string
}

export type TaskEventMetadata = TaskEventSource &
  (
    | { kind: "message"; status: null }
    | {
        kind: "completion"
        status: "success" | "error" | "timeout" | "interrupted"
      }
  )

const UUID_PATTERN =
  "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"

const eventSchema = schema as Exclude<
  ConstructorParameters<typeof Validator>[0],
  boolean
>
const validator = new Validator({
  ...eventSchema,
  required: Object.keys(schema.properties),
  properties: {
    ...eventSchema.properties,
    task_id: { type: "string", pattern: UUID_PATTERN },
    sender_thread_id: { type: "string", pattern: UUID_PATTERN },
  },
  oneOf: [
    { properties: { kind: { const: "message" }, status: { type: "null" } } },
    {
      properties: {
        kind: { const: "completion" },
        status: { enum: ["success", "error", "timeout", "interrupted"] },
      },
    },
  ],
})

export function taskEventMetadata(
  attributes: Record<string, string>
): TaskEventMetadata | undefined {
  if (
    attributes.sender !== "system:event-subscription" ||
    attributes.kind !== "system" ||
    attributes.surface !== "automation" ||
    !new RegExp(UUID_PATTERN).test(attributes.event_match ?? "") ||
    !attributes.task_event
  )
    return undefined

  try {
    const value: unknown = JSON.parse(attributes.task_event)
    return validator.validate(value).valid
      ? (value as TaskEventMetadata)
      : undefined
  } catch {
    return undefined
  }
}
