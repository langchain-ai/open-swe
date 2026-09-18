import { describe, expect, it } from "vitest"

import { buildProfileUpdate } from "./profile"

describe("buildProfileUpdate", () => {
  it("preserves the subagent setting when another field changes", () => {
    const update = buildProfileUpdate(
      {
        default_model: "openai:gpt-5",
        reasoning_effort: "medium",
        disable_subagents: true,
      },
      { draft_prs: false },
      "openai:gpt-5",
      "medium"
    )

    expect(update.disable_subagents).toBe(true)
  })
})
