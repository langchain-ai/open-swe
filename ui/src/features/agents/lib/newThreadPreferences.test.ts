// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest"
import { newThreadPreferences } from "./newThreadPreferences"

beforeEach(() => window.localStorage.clear())

it("restores new-thread choices after reload without mixing repositories or accounts", async () => {
  newThreadPreferences.selectRepo("alice", "org/one")
  newThreadPreferences.selectRepo("bob", null)
  newThreadPreferences.selectBranch("/one", "feature")
  newThreadPreferences.selectBranch("/two", "main")
  newThreadPreferences.selectWorkspaceMode("worktree")

  vi.resetModules()
  const { newThreadPreferences: restored } =
    await import("./newThreadPreferences")
  expect(restored.repo("alice")).toBe("org/one")
  expect(restored.repo("bob")).toBeNull()
  expect(restored.repo("other")).toBeUndefined()
  expect(restored.branch("/one")).toBe("feature")
  expect(restored.branch("/two")).toBe("main")
  expect(restored.workspaceMode()).toBe("worktree")
})

it("keeps the composer usable when storage is unavailable or corrupt", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
  window.localStorage.setItem("open-swe.new-thread", "{")
  expect(newThreadPreferences.workspaceMode()).toBe("local")
  const write = vi
    .spyOn(Storage.prototype, "setItem")
    .mockImplementation(() => {
      throw new DOMException("Quota exceeded", "QuotaExceededError")
    })
  expect(() =>
    newThreadPreferences.selectWorkspaceMode("worktree")
  ).not.toThrow()
  expect(warn).toHaveBeenCalled()
  write.mockRestore()
  warn.mockRestore()
})
