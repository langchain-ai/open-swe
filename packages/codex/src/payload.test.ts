import { describe, expect, it } from "vitest"

import { adaptCodexPayload, requiresResponsesLite } from "./payload.js"

describe("requiresResponsesLite", () => {
  it("selects the model families Codex serves over responses-lite", () => {
    for (const model of [
      "gpt-5.6-sol",
      "gpt-5.6-terra",
      "gpt-5.6-luna",
      "gpt-daybreak-blue-latest",
      "codex-auto-review",
    ]) {
      expect(requiresResponsesLite(model), model).toBe(true)
    }
    for (const model of ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.2"]) {
      expect(requiresResponsesLite(model), model).toBe(false)
    }
    expect(requiresResponsesLite(undefined)).toBe(false)
  })
})

describe("adaptCodexPayload", () => {
  it("lifts developer and system turns into instructions", () => {
    expect(
      adaptCodexPayload({
        model: "gpt-5.6-sol",
        input: [
          { type: "message", role: "developer", content: "Be careful." },
          { type: "message", role: "user", content: "List the changes." },
        ],
      })
    ).toEqual({
      model: "gpt-5.6-sol",
      instructions: "Be careful.",
      input: [{ type: "message", role: "user", content: "List the changes." }],
    })
  })

  it("joins multiple instruction turns and reads structured content parts", () => {
    const adapted = adaptCodexPayload({
      input: [
        { role: "system", content: [{ type: "input_text", text: "First." }] },
        { role: "developer", content: "Second." },
        { role: "user", content: "Go." },
      ],
    })

    expect(adapted.instructions).toBe("First.\n\nSecond.")
    expect(adapted.input).toEqual([{ role: "user", content: "Go." }])
  })

  it("keeps instructions the caller already set", () => {
    const adapted = adaptCodexPayload({
      instructions: "Explicit.",
      input: [{ role: "developer", content: "Lifted." }],
    })

    expect(adapted.instructions).toBe("Explicit.")
    expect(adapted.input).toEqual([])
  })

  it("leaves a payload without instruction turns untouched", () => {
    const payload = { model: "gpt-5.6-sol", input: [{ role: "user", content: "Go." }] }

    expect(adaptCodexPayload(payload)).toBe(payload)
  })
})
