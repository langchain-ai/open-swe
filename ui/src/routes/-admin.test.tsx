/** @vitest-environment jsdom */

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { ModelOption } from "@/lib/api"
import {
  SlackIntegrationSection,
  updateFamilyAllowlist,
  updateModelAllowlist,
} from "./admin"

const STORAGE_KEY = "open-swe.admin.slack-code-channels-enabled"
const storage = new Map<string, string>()
const localStorage = {
  clear: () => storage.clear(),
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
}

const MODELS: Array<ModelOption> = [
  {
    id: "openai:gpt-5.6-sol",
    label: "GPT-5.6 Sol",
    family: "openai",
    family_label: "OpenAI",
    efforts: ["medium"],
    default_effort: "medium",
    supports_images: true,
  },
  {
    id: "openai:gpt-5.6-luna",
    label: "GPT-5.6 Luna",
    family: "openai",
    family_label: "OpenAI",
    efforts: ["medium"],
    default_effort: "medium",
    supports_images: true,
  },
  {
    id: "anthropic:claude-opus-5",
    label: "Opus 5",
    family: "anthropic",
    family_label: "Anthropic",
    efforts: ["high"],
    default_effort: "high",
    supports_images: true,
  },
]

describe("model availability allowlists", () => {
  it("stores family switches as provider wildcards", () => {
    expect(
      updateFamilyAllowlist(MODELS, null, "openai", MODELS.slice(0, 2), false)
    ).toEqual(["anthropic:claude-opus-5"])
    expect(
      updateFamilyAllowlist(
        MODELS,
        ["anthropic:claude-opus-5"],
        "openai",
        MODELS.slice(0, 2),
        true
      )
    ).toEqual(["anthropic:claude-opus-5", "openai:*"])
  })

  it("expands a family wildcard when one model is disabled", () => {
    expect(
      updateModelAllowlist(
        MODELS,
        ["openai:*", "anthropic:*"],
        MODELS[0]!,
        false
      )
    ).toEqual(["openai:gpt-5.6-luna", "anthropic:*"])
  })
})

describe("SlackIntegrationSection", () => {
  const writeText = vi
    .fn<(value: string) => Promise<void>>()
    .mockResolvedValue(undefined)

  beforeEach(() => {
    localStorage.clear()
    writeText.mockClear()
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: localStorage,
    })
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    })
  })

  it("defaults to legacy Slack and copies Code Channels only when enabled", async () => {
    render(<SlackIntegrationSection />)

    const toggle = screen.getByRole("switch", {
      name: /^Slack Code Channels/,
    })
    expect(toggle.getAttribute("aria-checked")).toBe("false")

    fireEvent.click(screen.getByRole("button", { name: "Copy manifest" }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    expect(JSON.parse(writeText.mock.calls[0]![0]).features).not.toHaveProperty(
      "code_channels"
    )

    fireEvent.click(toggle)
    expect(localStorage.getItem(STORAGE_KEY)).toBe("true")
    fireEvent.click(screen.getByRole("button", { name: "Copy manifest" }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2))
    expect(
      JSON.parse(writeText.mock.calls[1]![0]).features.code_channels.enabled
    ).toBe(true)
  })
})
