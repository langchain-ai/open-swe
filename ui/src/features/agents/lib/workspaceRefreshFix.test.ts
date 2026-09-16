/** @vitest-environment jsdom */

import { afterEach, describe, expect, it } from "vitest"

import {
  consumeWorkspaceRefreshFix,
  stageWorkspaceRefreshFix,
} from "./workspaceRefreshFix"

const workspace = {
  slug: "preview",
  name: "Preview",
  repos: [],
  slack_channel_ids: [],
  is_default: false,
  has_snapshot: true,
  refresh_status: "failed" as const,
  refresh_kind: "full" as const,
  refresh_finished_at: "2026-09-16T15:00:00Z",
  refresh_error: "setup script exited 2",
  refresh_log_excerpt: "pnpm install failed",
  refresh_steps: [{ label: "setup", status: "failed" as const, exit_code: 2 }],
}

afterEach(() => sessionStorage.clear())

describe("workspace refresh fix handoff", () => {
  it("stages failure details once without putting them in the URL", () => {
    const id = stageWorkspaceRefreshFix(workspace)
    const staged = consumeWorkspaceRefreshFix(id)

    expect(staged?.workspace).toBe("preview")
    expect(staged?.prompt).toContain("setup script exited 2")
    expect(staged?.prompt).toContain("setup: failed, exit 2")
    expect(staged?.prompt).toContain("pnpm install failed")
    expect(consumeWorkspaceRefreshFix(id)).toBeNull()
  })
})
