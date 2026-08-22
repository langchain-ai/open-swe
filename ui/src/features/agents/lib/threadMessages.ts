import type { Message } from "./types"

export function selectThreadMessages(
  hydrated: Array<Message>,
  optimistic: Array<Message>,
  hydratedThread: boolean
): Array<Message> {
  return hydratedThread && hydrated.length > 0 ? hydrated : optimistic
}
