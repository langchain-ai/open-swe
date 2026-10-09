import { AIMessage, HumanMessage } from "@langchain/core/messages"
import { describe, expect, it } from "vitest"

import {
  contextUsageFromUsageMetadata,
  formatTokenCount,
  latestContextUsage,
  turnCosts,
} from "./contextUsage"
import type { Message } from "./types"

describe("context usage helpers", () => {
  it("prefers input plus output tokens", () => {
    expect(
      contextUsageFromUsageMetadata({
        input_tokens: 12_000,
        output_tokens: 345,
        total_tokens: 1,
      })
    ).toEqual({ tokens: 12_345, model: null })
  })

  it("falls back to total tokens", () => {
    expect(
      contextUsageFromUsageMetadata({ total_tokens: 42_000 })?.tokens
    ).toBe(42_000)
  })

  it("returns null without usage metadata", () => {
    expect(contextUsageFromUsageMetadata(undefined)).toBeNull()
    expect(latestContextUsage([new HumanMessage("hello")])).toBeNull()
  })

  it("uses the newest AI message and its model", () => {
    const messages = [
      new AIMessage({
        content: "old",
        usage_metadata: { input_tokens: 1, output_tokens: 2, total_tokens: 3 },
      }),
      new HumanMessage("next"),
      new AIMessage({
        content: "new",
        response_metadata: { model_name: "claude-opus-4-5" },
        usage_metadata: {
          input_tokens: 97,
          output_tokens: 2,
          total_tokens: 99,
        },
      }),
    ]

    expect(latestContextUsage(messages)).toEqual({
      tokens: 99,
      model: "claude-opus-4-5",
    })
  })

  it("formats token counts compactly", () => {
    expect(formatTokenCount(999)).toBe("999")
    expect(formatTokenCount(1_200)).toBe("1.2K")
    expect(formatTokenCount(1_200_000)).toBe("1.2M")
  })

  it("counts each run once, on the last turn it ran in", () => {
    const turn = (id: string, invocationIds: Array<string>): Message => ({
      id,
      author: "agent",
      timestamp: "",
      chunks: [],
      invocationIds,
    })
    const costs = turnCosts(
      [turn("a", ["r1"]), turn("b", ["r1"]), turn("c", ["r2", "r3"])],
      { r1: 1, r2: 2, r3: 3 }
    )
    expect([...costs]).toEqual([
      ["b", 1],
      ["c", 5],
    ])
  })
})
