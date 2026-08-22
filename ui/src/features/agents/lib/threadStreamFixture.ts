import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages"
import type { BaseMessage } from "@langchain/core/messages"
import type { AssembledToolCall } from "@langchain/react"

/**
 * Synthetic `stream.messages` / `stream.toolCalls` state for benchmarks and
 * render tests. Mirrors the SDK's identity semantics: a streaming flush
 * replaces only the streaming message's slot with a fresh object while every
 * other element keeps its reference, and `toolCalls` changes only on tool
 * lifecycle events — never on token deltas.
 */
export interface ThreadStreamFixture {
  messages: Array<BaseMessage>
  toolCalls: Array<AssembledToolCall>
}

const BASE_TIME = Date.parse("2026-01-01T00:00:00.000Z")

function isoAt(offsetSeconds: number): string {
  return new Date(BASE_TIME + offsetSeconds * 1000).toISOString()
}

/** The SDK surfaces server `created_at` on the raw message object. */
function stamp<T extends BaseMessage>(message: T, iso: string): T {
  Object.assign(message, { created_at: iso })
  return message
}

function fileBody(turn: number, lines: number, edited: boolean): string {
  const rows: Array<string> = []
  for (let i = 0; i < lines; i += 1) {
    rows.push(
      edited && i % 7 === 3
        ? `  const value${i} = resolve(${turn}, ${i})`
        : `  const value${i} = compute(${turn}, ${i})`
    )
  }
  return `export function turn${turn}(): void {\n${rows.join("\n")}\n}`
}

function replyMarkdown(turn: number, extraSentences = 0): string {
  const extra = Array.from(
    { length: extraSentences },
    (_, i) => `Streamed sentence ${i + 1} lands here with a bit more detail.`
  ).join(" ")
  return [
    `### Turn ${turn} summary`,
    ``,
    `Updated the \`parser\` so nested blocks resolve before the fallback path runs. The cursor now stays stable across retries, which removes the duplicated-token symptom.`,
    ``,
    `- rewrote \`resolveBlock\` to return early on balanced input`,
    `- added a regression test for unterminated fences`,
    `- kept the public API unchanged`,
    ``,
    "```ts",
    `export function resolveBlock(input: string, depth = ${turn % 5}): string {`,
    `  if (!input.includes("\\n")) return input`,
    `  return input.split("\\n").map((line) => line.trimEnd()).join("\\n")`,
    `}`,
    "```",
    ``,
    `See [the change](https://example.com/diff/${turn}) for details.${extra ? ` ${extra}` : ""}`,
  ].join("\n")
}

interface FixtureToolCall {
  id: string
  name: string
  args: Record<string, unknown>
  output: string
}

function turnToolCalls(turn: number): Array<FixtureToolCall> {
  const calls: Array<FixtureToolCall> = []
  for (let i = 0; i < 3; i += 1) {
    calls.push({
      id: `t${turn}-read-${i}`,
      name: "read_file",
      args: { file_path: `src/features/module${turn % 9}/file${i}.ts` },
      output: fileBody(turn, 40, false),
    })
  }
  calls.push({
    id: `t${turn}-grep`,
    name: "grep",
    args: { pattern: `resolveBlock${turn}`, path: "src" },
    output: `src/features/module${turn % 9}/file0.ts:12: resolveBlock${turn}(input)`,
  })
  calls.push({
    id: `t${turn}-edit`,
    name: "edit_file",
    args: {
      file_path: `src/features/module${turn % 9}/file0.ts`,
      old_string: fileBody(turn, 30, false),
      new_string: fileBody(turn, 30, true),
    },
    output: "ok",
  })
  for (let i = 0; i < 2; i += 1) {
    calls.push({
      id: `t${turn}-run-${i}`,
      name: "execute",
      args: { command: `pnpm vitest run src/features/module${turn % 9}` },
      output: `Test Files  ${i + 1} passed (${i + 1})\n     Tests  ${12 + i} passed (${12 + i})`,
    })
  }
  calls.push({
    id: `t${turn}-fetch`,
    name: "fetch_url",
    args: { url: `https://example.com/docs/${turn}` },
    output: `# Docs page ${turn}\n\nReference content for turn ${turn}.`,
  })
  return calls
}

export function buildThreadFixture(turnCount: number): ThreadStreamFixture {
  const messages: Array<BaseMessage> = []
  const toolCalls: Array<AssembledToolCall> = []

  for (let turn = 0; turn < turnCount; turn += 1) {
    const t0 = turn * 100
    messages.push(
      stamp(
        new HumanMessage({
          id: `user-${turn}`,
          content: `Please fix the parser regression in module${turn % 9} and add a test for turn ${turn}.`,
        }),
        isoAt(t0)
      )
    )

    const calls = turnToolCalls(turn)
    messages.push(
      stamp(
        new AIMessage({
          id: `ai-${turn}-work`,
          content: "",
          tool_calls: calls.map((call) => ({
            id: call.id,
            name: call.name,
            args: call.args,
            type: "tool_call" as const,
          })),
        }),
        isoAt(t0 + 5)
      )
    )
    calls.forEach((call, index) => {
      messages.push(
        stamp(
          new ToolMessage({
            id: `${call.id}-result`,
            tool_call_id: call.id,
            content: call.output,
            status: "success",
          }),
          isoAt(t0 + 10 + index)
        )
      )
      toolCalls.push({
        name: call.name,
        callId: call.id,
        id: call.id,
        namespace: [],
        input: call.args,
        args: call.args,
        output: call.output,
        status: "finished",
        error: undefined,
      })
    })

    messages.push(
      stamp(
        new AIMessage({ id: `ai-${turn}-reply`, content: replyMarkdown(turn) }),
        isoAt(t0 + 60)
      )
    )
  }

  return { messages, toolCalls }
}

/**
 * Successive stream flushes growing the final reply. Snapshot N shares every
 * message reference with the base fixture except the last reply, which is a
 * fresh `AIMessage` per flush — exactly what the SDK's delta path produces.
 */
export function streamTicks(
  fixture: ThreadStreamFixture,
  tickCount: number
): Array<ThreadStreamFixture> {
  const last = fixture.messages[fixture.messages.length - 1]
  if (!(last instanceof AIMessage)) {
    throw new Error("fixture must end with a streaming AI reply")
  }
  const lastTurn = Math.round(
    (fixture.messages.filter((m) => m instanceof HumanMessage).length || 1) - 1
  )
  const createdAt = (last as unknown as { created_at?: string }).created_at

  const snapshots: Array<ThreadStreamFixture> = []
  for (let tick = 1; tick <= tickCount; tick += 1) {
    const messages = fixture.messages.slice()
    const grown = new AIMessage({
      id: last.id,
      content: replyMarkdown(lastTurn, tick),
    })
    messages[messages.length - 1] = createdAt ? stamp(grown, createdAt) : grown
    snapshots.push({ messages, toolCalls: fixture.toolCalls })
  }
  return snapshots
}
