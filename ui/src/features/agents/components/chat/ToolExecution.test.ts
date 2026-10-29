import { describe, expect, it } from "vitest"

import {
  formatToolDisplay,
  formatToolDisplayParts,
} from "./toolExecutionDisplay"

describe("formatToolDisplay", () => {
  const repoPath = "/workspace/open-swe"

  it("renders only the read file name and retains its full path", () => {
    const fullPath = "/workspace/open-swe/ui/src/AGENTS.md"

    expect(
      formatToolDisplay(
        `read_file ${fullPath}`,
        "read",
        { file_path: fullPath },
        repoPath
      )
    ).toBe("Read AGENTS.md")
    expect(
      formatToolDisplayParts(
        `read_file ${fullPath}`,
        "read",
        { file_path: fullPath },
        repoPath
      )
    ).toEqual({
      heading: "Read",
      preview: "AGENTS.md",
      previewTooltip: fullPath,
    })
  })

  it("renders ls with only the target directory name", () => {
    expect(
      formatToolDisplay(
        "ls /workspace/open-swe/ui/src",
        "read",
        { path: "/workspace/open-swe/ui/src" },
        repoPath
      )
    ).toBe("List src")
  })

  it("renders search tools with their pattern", () => {
    expect(
      formatToolDisplay("grep", "search", { pattern: "tool_calls" }, repoPath)
    ).toBe('Search "tool_calls"')
  })

  it.each([
    ["cd /workspace/open-swe && pnpm lint", "pnpm lint"],
    ["cd '/workspace/my repo'; pnpm lint", "pnpm lint"],
    ['cd "/workspace/my repo"\npnpm lint', "pnpm lint"],
    ["cd -- /workspace/my\\ repo && cd ui && pnpm lint", "pnpm lint"],
    ["cd /workspace && echo 'a && b'; cd ui", "echo 'a && b'; cd ui"],
    ["cd /workspace", "cd /workspace"],
    ["cd /workspace &&", "cd /workspace &&"],
    ["cd /workspace || exit 1", "cd /workspace || exit 1"],
    [
      "cd $(prepare_workspace) && pnpm lint",
      "cd $(prepare_workspace) && pnpm lint",
    ],
    ["echo cd /workspace && pnpm lint", "echo cd /workspace && pnpm lint"],
  ])("formats shell display for %s", (command, expected) => {
    const input = { command }
    expect(formatToolDisplay("execute", "execute", input)).toBe(
      `Shell ${expected}`
    )
    expect(input.command).toBe(command)
  })

  it("sentence-cases raw tool names", () => {
    expect(formatToolDisplay("save_plan", "other", {}, repoPath)).toBe(
      "Save plan"
    )
    expect(formatToolDisplay("slack_reply", "other", {}, repoPath)).toBe(
      "Slack reply"
    )
  })
})
