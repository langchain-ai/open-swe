/** @vitest-environment jsdom */

import { act, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { ModelOption, Profile } from "@/lib/api"

const mocks = vi.hoisted(() => ({
  profile: undefined as Profile | undefined,
  save: vi.fn(),
}))

vi.mock("@/lib/profile", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/profile")>()),
  useOptions: () => ({
    data: {
      models: MODELS,
      default_agent_model: "openai:gpt-5.6-sol",
      default_agent_reasoning_effort: "medium",
    },
    isLoading: false,
  }),
  useProfile: () => ({ data: mocks.profile, isLoading: false }),
  useSaveProfile: () => ({ mutate: mocks.save }),
}))

import { useModelOptions } from "./useModelOptions"

const MODELS: Array<ModelOption> = [
  {
    id: "openai:gpt-5.6-sol",
    label: "GPT-5.6 Sol",
    efforts: ["low", "medium", "high"],
    default_effort: "high",
    supports_images: true,
  },
  {
    id: "fireworks:accounts/fireworks/models/kimi-k3",
    label: "Kimi K3",
    efforts: ["low", "high"],
    default_effort: "high",
    supports_images: false,
  },
]

afterEach(() => {
  mocks.profile = undefined
  mocks.save.mockReset()
})

describe("useModelOptions", () => {
  it("defaults to Auto until the user opts out of routing", () => {
    const { result } = renderHook(() => useModelOptions())
    expect(result.current.defaultSelection).toBeNull()
  })

  it("uses the profile default model when routing is disabled", () => {
    mocks.profile = {
      model_routing_enabled: false,
      default_model: "fireworks:accounts/fireworks/models/kimi-k3",
      reasoning_effort: "low",
    }
    const { result } = renderHook(() => useModelOptions())
    expect(result.current.defaultSelection).toEqual({
      modelId: "fireworks:accounts/fireworks/models/kimi-k3",
      effort: "low",
    })
  })

  it("falls back to the team default when routing is off and the profile model is unsupported", () => {
    mocks.profile = {
      model_routing_enabled: false,
      default_model: "retired:model",
      reasoning_effort: "low",
    }
    const { result } = renderHook(() => useModelOptions())
    expect(result.current.defaultSelection).toEqual({
      modelId: "openai:gpt-5.6-sol",
      effort: "medium",
    })
  })

  it("persists an explicit pick as the profile default with routing off", () => {
    const { result } = renderHook(() => useModelOptions())
    act(() =>
      result.current.persistSelection({
        modelId: "fireworks:accounts/fireworks/models/kimi-k3",
        effort: "high",
      })
    )
    expect(mocks.save).toHaveBeenCalledWith(
      expect.objectContaining({
        model_routing_enabled: false,
        default_model: "fireworks:accounts/fireworks/models/kimi-k3",
        reasoning_effort: "high",
      })
    )
  })

  it("persists Auto by re-enabling routing without touching the default model", () => {
    mocks.profile = {
      model_routing_enabled: false,
      default_model: "fireworks:accounts/fireworks/models/kimi-k3",
      reasoning_effort: "low",
    }
    const { result } = renderHook(() => useModelOptions())
    act(() => result.current.persistSelection(null))
    expect(mocks.save).toHaveBeenCalledWith(
      expect.objectContaining({
        model_routing_enabled: true,
        default_model: "fireworks:accounts/fireworks/models/kimi-k3",
        reasoning_effort: "low",
      })
    )
  })
})
