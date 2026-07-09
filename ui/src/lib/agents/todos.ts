import type { TodoItem } from "./types"

export function todosFromState(value: unknown): Array<TodoItem> {
  if (!Array.isArray(value)) return []

  const todos: Array<TodoItem> = []
  for (const item of value) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return []
    const { content, status } = item as Record<string, unknown>
    if (
      typeof content !== "string" ||
      !content.trim() ||
      (status !== "pending" &&
        status !== "in_progress" &&
        status !== "completed")
    ) {
      return []
    }
    todos.push({ content: content.trim(), status })
  }
  return todos
}
