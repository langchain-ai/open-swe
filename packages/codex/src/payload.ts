/**
 * Request adaptations the Codex backend requires but `ChatOpenAI` does not make
 * on its own. Mirrors `langchain_openai.chat_models.codex._ChatOpenAICodex`,
 * which has no JavaScript counterpart.
 */

import { z } from "zod"

export const RESPONSES_LITE_HEADER = "x-openai-internal-codex-responses-lite"

/**
 * An input turn Codex refuses to accept as a turn. Matched with `safeParse`
 * rather than used to rewrite the payload: anything not recognized here has to
 * survive untouched, since this adapter sits in front of every request.
 */
const InstructionItem = z.object({
  role: z.enum(["system", "developer"]),
  content: z.unknown(),
})

const TextPart = z.object({ text: z.string() })

/**
 * Codex serves these models only over its responses-lite protocol; the older
 * families reject the header. Mirrors `use_responses_lite` in Codex's model
 * catalog.
 */
export function requiresResponsesLite(model: unknown): boolean {
  if (typeof model !== "string") return false
  return (
    model.startsWith("gpt-5.6-") ||
    model.startsWith("gpt-daybreak-") ||
    model === "codex-auto-review"
  )
}

function itemText(content: unknown): string {
  if (typeof content === "string") return content
  if (!Array.isArray(content)) return ""
  return content
    .map((part) => TextPart.safeParse(part).data?.text ?? "")
    .filter(Boolean)
    .join("\n")
}

/**
 * Lift system/developer turns into top-level `instructions`. Codex rejects
 * instruction-role turns, and rejects a request that carries no `instructions`.
 */
export function adaptCodexPayload(
  payload: Record<string, unknown>
): Record<string, unknown> {
  const input = payload.input
  if (!Array.isArray(input)) return payload

  const instructions = input.map((item) => InstructionItem.safeParse(item))
  if (!instructions.some((result) => result.success)) return payload

  const lifted = instructions
    .map((result) => (result.success ? itemText(result.data.content) : ""))
    .filter(Boolean)
    .join("\n\n")

  return {
    ...payload,
    instructions: payload.instructions ?? lifted,
    input: input.filter((_, index) => !instructions[index]!.success),
  }
}
