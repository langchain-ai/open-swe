import { describe, expect, it } from "vitest"

import { resolveCodingModel } from "./coding-agent.js"

describe("resolveCodingModel", () => {
  it("uses only the supported OpenAI model path", () => {
    const selected = resolveCodingModel({
      agent_model_id: "openai:gpt-test",
      agent_effort: "xhigh",
    })
    const unsupported = resolveCodingModel({
      agent_model_id: "anthropic:claude-test",
      agent_effort: "max",
    })

    expect(selected.model).toBe("gpt-test")
    expect(selected.zdrEnabled).toBe(true)
    expect(selected.modelKwargs).toEqual({
      include: ["reasoning.encrypted_content"],
    })
    expect(unsupported.model).toBe("gpt-5.6-sol")
  })
})
