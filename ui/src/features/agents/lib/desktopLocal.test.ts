/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"

import { ensureDesktopModelCredential } from "./desktopLocal"

describe("ensureDesktopModelCredential", () => {
  const originalDesktop = window.openSweDesktop

  afterEach(() => {
    Object.defineProperty(window, "openSweDesktop", {
      configurable: true,
      value: originalDesktop,
    })
  })

  const setDesktop = (
    value: Partial<NonNullable<typeof window.openSweDesktop>>
  ) => {
    Object.defineProperty(window, "openSweDesktop", {
      configurable: true,
      value,
    })
  }

  it("opens sign-in when an OpenAI model has no API key", async () => {
    const signInLocalOpenAI = vi.fn().mockResolvedValue({ signedIn: true })
    setDesktop({
      localModelCredentialStatus: vi.fn().mockResolvedValue({
        available: false,
        variable: "OPENAI_API_KEY",
        canSignIn: true,
      }),
      signInLocalOpenAI,
    })

    await expect(
      ensureDesktopModelCredential("openai:gpt-test")
    ).resolves.toBeNull()
    expect(signInLocalOpenAI).toHaveBeenCalledOnce()
  })

  it("keeps the environment-variable guidance for other providers", async () => {
    setDesktop({
      localModelCredentialStatus: vi.fn().mockResolvedValue({
        available: false,
        variable: "ANTHROPIC_API_KEY",
      }),
    })

    await expect(ensureDesktopModelCredential("anthropic:test")).resolves.toBe(
      "Set ANTHROPIC_API_KEY in the environment before starting Open SWE."
    )
  })
})
