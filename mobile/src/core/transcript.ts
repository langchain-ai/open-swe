import { shownText } from "./inputEnvelope"
import { isObject, messageText, stringAt, type JSONValue } from "./models"

export type TodoStatus = "pending" | "in_progress" | "completed"

export interface TodoItem {
  content: string
  status: TodoStatus
}

export function decodeTodo(value: unknown): TodoItem | null {
  const content = stringAt(value, "content")
  if (!content) return null
  const status = stringAt(value, "status")
  return {
    content,
    status:
      status === "in_progress" || status === "completed" ? status : "pending",
  }
}

export type ToolKind = "read" | "search" | "edit" | "command" | "other"

/** One tool call the agent made, reduced to what a row shows. */
export interface ToolActivity {
  name: string
  detail?: string
  kind: ToolKind
}

const DETAIL_KEYS = [
  "file_path",
  "path",
  "pattern",
  "command",
  "query",
  "url",
  "description",
]

export function toolActivity(name: string, detail?: string): ToolActivity {
  return { name, detail, kind: toolKind(name) }
}

/** A tool call from its name and arguments. */
export function toolActivityFromCall(
  name: string,
  args: unknown
): ToolActivity {
  const detail = DETAIL_KEYS.map((key) => stringAt(args, key)).find(
    (value) => value !== undefined
  )
  return toolActivity(name, detail)
}

function toolKind(raw: string): ToolKind {
  const name = raw.toLowerCase()
  const has = (parts: string[]) => parts.some((part) => name.includes(part))
  if (has(["grep", "search", "find"])) return "search"
  if (has(["edit", "write", "patch", "str_replace", "create_file"]))
    return "edit"
  if (name === "ls" || has(["read", "glob", "view", "list_"])) return "read"
  if (has(["execute", "bash", "shell", "run_command"])) return "command"
  return "other"
}

/** A run of consecutive tool calls, summarised the way the transcript shows them. */
export interface ActivityGroup {
  tools: ToolActivity[]
}

export function activityVerb(group: ActivityGroup): string {
  return group.tools.some((tool) => tool.kind === "edit")
    ? "Edited"
    : "Explored"
}

export function activitySummary(group: ActivityGroup): string {
  const ofKind = (kind: ToolKind) =>
    group.tools.filter((tool) => tool.kind === kind)
  const edited = new Set(
    ofKind("edit")
      .map((tool) => tool.detail)
      .filter(Boolean)
  ).size
  const read = ofKind("read").length
  const searches = ofKind("search").length
  const commands = ofKind("command").length
  const other = ofKind("other").length
  const count = (n: number, noun: string, plural = `${noun}s`) =>
    `${n} ${n === 1 ? noun : plural}`
  const parts: string[] = []
  if (edited > 0) parts.push(count(edited, "file"))
  if (read > 0)
    parts.push((edited > 0 ? "explored " : "") + count(read, "file"))
  if (searches > 0) parts.push(count(searches, "search", "searches"))
  if (commands > 0) parts.push(count(commands, "command"))
  if (other > 0) parts.push(count(other, "other tool"))
  return parts.join(", ")
}

export type TranscriptItem =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "assistant"; text: string }
  | { id: string; kind: "activity"; group: ActivityGroup }
  | { id: string; kind: "todos"; todos: TodoItem[] }

/**
 * Collects transcript entries in order: consecutive tool calls fold into one
 * activity row, and the to-do list shows once, where it was last written.
 */
export class TranscriptBuilder {
  private items: TranscriptItem[] = []
  private pending: ToolActivity[] = []
  private pendingID = ""
  private todosIndex: number | null = null

  user(id: string, raw: string) {
    const text = shownText(raw)
    if (!text) return
    this.flush()
    this.items.push({ id, kind: "user", text })
  }

  assistant(id: string, raw: string) {
    const text = raw.trim()
    if (!text) return
    this.flush()
    this.items.push({ id, kind: "assistant", text })
  }

  tool(id: string, name: string, args: unknown) {
    if (name === "write_todos") {
      this.flush()
      const todos =
        isObject(args) && Array.isArray(args.todos)
          ? args.todos.map(decodeTodo).filter((todo) => todo !== null)
          : []
      this.todosIndex = this.items.length
      this.items.push({ id, kind: "todos", todos })
      return
    }
    if (this.pending.length === 0) this.pendingID = id
    this.pending.push(toolActivityFromCall(name, args))
  }

  finish(): TranscriptItem[] {
    this.flush()
    const keep = this.todosIndex
    return this.items.filter(
      (item, index) => item.kind !== "todos" || index === keep
    )
  }

  private flush() {
    if (this.pending.length === 0) return
    this.items.push({
      id: this.pendingID,
      kind: "activity",
      group: { tools: this.pending },
    })
    this.pending = []
  }
}

/** The transcript of a thread without an event log, from its LangGraph `…/state`. */
export function transcriptFromState(state: unknown): TranscriptItem[] {
  const values = isObject(state) ? state.values : undefined
  const messages =
    isObject(values) && Array.isArray(values.messages) ? values.messages : []
  const builder = new TranscriptBuilder()
  messages.forEach((message: JSONValue, index) => {
    if (!isObject(message)) return
    const id = stringAt(message, "id") ?? `m${index}`
    const role = stringAt(message, "type") ?? stringAt(message, "role")
    if (role === "human" || role === "user") {
      builder.user(id, messageText(message.content))
    } else if (
      role === "ai" ||
      role === "assistant" ||
      role === "AIMessageChunk"
    ) {
      builder.assistant(id, messageText(message.content))
      const calls = Array.isArray(message.tool_calls) ? message.tool_calls : []
      calls.forEach((call, offset) => {
        if (isObject(call))
          builder.tool(
            `${id}-tools-${offset}`,
            stringAt(call, "name") ?? "tool",
            call.args
          )
      })
    }
  })
  return builder.finish()
}
