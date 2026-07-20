import { describe, expect, it } from "vitest";

import { formatToolDisplay } from "./toolExecutionDisplay";

describe("formatToolDisplay", () => {
  const projectPath = "/workspace/open-swe";

  it("renders read_file with the file_path alias consistently", () => {
    expect(
      formatToolDisplay(
        "read_file /workspace/open-swe/AGENTS.md",
        "read",
        { file_path: "/workspace/open-swe/AGENTS.md" },
        projectPath,
      ),
    ).toBe("Read AGENTS.md");
  });

  it("renders ls as a list operation with a relative path", () => {
    expect(
      formatToolDisplay(
        "ls /workspace/open-swe/ui",
        "read",
        { path: "/workspace/open-swe/ui" },
        projectPath,
      ),
    ).toBe("List ui");
  });

  it("renders search tools with their pattern", () => {
    expect(
      formatToolDisplay("grep", "search", { pattern: "tool_calls" }, projectPath),
    ).toBe('Search "tool_calls"');
  });

  it("normalizes write_todos", () => {
    expect(formatToolDisplay("write todos", "other", {}, projectPath)).toBe("Update todos");
  });

  it("sentence-cases raw tool names", () => {
    expect(formatToolDisplay("enter_plan_mode", "other", {}, projectPath)).toBe(
      "Enter plan mode",
    );
    expect(formatToolDisplay("save_plan", "other", {}, projectPath)).toBe("Save plan");
    expect(formatToolDisplay("slack_thread_reply", "other", {}, projectPath)).toBe(
      "Slack thread reply",
    );
  });
});
