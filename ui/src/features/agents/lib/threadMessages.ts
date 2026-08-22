import type { Message } from "./types"

export function threadMessages(
  hydrated: Array<Message>,
  optimistic: Array<Message>
): Array<Message> {
  return hydrated.length > 0 ? hydrated : optimistic
}
