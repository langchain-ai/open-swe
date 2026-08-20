import { AIMessage, HumanMessage } from "@langchain/core/messages"
import { describe, expect, it } from "vitest"

import {
  contextTokensFromUsageMetadata,
  formatTokenCount,
  latestContextTokens,
} from "./contextUsage"

describe("context usage helpers", () => {
  it("prefers input plus output tokens", () => {
    expect(
      contextTokensFromUsageMetadata({
        input_tokens: 12_000,
        output_tokens: 345,
        total_tokens: 1,
      })
    ).toBe(12_345)
  })

  it("falls back to total tokens", () => {
    expect(contextTokensFromUsageMetadata({ total_tokens: 42_000 })).toBe(
      42_000
    )
  })

  it("returns null without usage metadata", () => {
    expect(contextTokensFromUsageMetadata(undefined)).toBeNull()
    expect(latestContextTokens([new HumanMessage("hello")])).toBeNull()
  })

  it("uses the newest AI message", () => {
    const messages = [
      new AIMessage({
        content: "old",
        usage_metadata: { input_tokens: 1, output_tokens: 2, total_tokens: 3 },
      }),
      new HumanMessage("next"),
      new AIMessage({
        content: "new",
        usage_metadata: {
          input_tokens: 97,
          output_tokens: 2,
          total_tokens: 99,
        },
      }),
    ]

    expect(latestContextTokens(messages)).toBe(99)
  })

  it("formats token counts compactly", () => {
    expect(formatTokenCount(999)).toBe("999")
    expect(formatTokenCount(1_200)).toBe("1.2K")
    expect(formatTokenCount(1_200_000)).toBe("1.2M")
  })
})
