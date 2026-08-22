/**
 * Request adaptations the Codex backend requires but `ChatOpenAI` does not make
 * on its own. Mirrors `langchain_openai.chat_models.codex._ChatOpenAICodex`,
 * which has no JavaScript counterpart.
 */

export const RESPONSES_LITE_HEADER = "x-openai-internal-codex-responses-lite"

const INSTRUCTION_ROLES = new Set(["system", "developer"])

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
    .map((part) =>
      part && typeof part === "object" && typeof (part as { text?: unknown }).text === "string"
        ? (part as { text: string }).text
        : ""
    )
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

  const isInstruction = (item: unknown): boolean =>
    Boolean(
      item &&
        typeof item === "object" &&
        INSTRUCTION_ROLES.has(String((item as { role?: unknown }).role))
    )

  const instructionItems = input.filter(isInstruction)
  if (!instructionItems.length) return payload

  const lifted = instructionItems
    .map((item) => itemText((item as { content?: unknown }).content))
    .filter(Boolean)
    .join("\n\n")

  return {
    ...payload,
    instructions: payload.instructions ?? lifted,
    input: input.filter((item) => !isInstruction(item)),
  }
}
