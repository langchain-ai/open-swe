import { render } from "ink-testing-library";
import { AssistantToolUseMessage } from "./AssistantToolUseMessage.js";
import type { Chunk } from "@types";

describe("AssistantToolUseMessage", () => {
  it("renders edit_file calls as Update(path) with a diff body", () => {
    const chunk: Chunk = {
      kind: "tool-execution",
      toolCallId: "tool-1",
      toolName: "edit_file",
      toolArgs: {
        file_path: "/tmp/example.ts",
        old_string: "const value = 1;",
        new_string: "const value = 2;",
      },
      status: "success",
      output: "Successfully replaced 1 occurrence(s) in '/tmp/example.ts'",
    };

    const { lastFrame } = render(<AssistantToolUseMessage chunk={chunk} />);
    const frame = lastFrame() ?? "";

    expect(frame).toContain("Update(");
    expect(frame).toContain("/tmp/example.ts");
    expect(frame).toContain("-const value = 1;");
    expect(frame).toContain("+const value = 2;");
    expect(frame).not.toContain("old_string=");
    expect(frame).not.toContain("new_string=");
  });

  it("renders bash calls as Bash(command) with output indented under ⎿", () => {
    const chunk: Chunk = {
      kind: "tool-execution",
      toolCallId: "tool-bash",
      toolName: "execute",
      toolArgs: { command: "echo hello" },
      status: "success",
      output: "hello\n",
    };

    const { lastFrame } = render(<AssistantToolUseMessage chunk={chunk} />);
    const frame = lastFrame() ?? "";

    expect(frame).toContain("Bash(");
    expect(frame).toContain("echo hello");
    expect(frame).toContain("⎿");
    expect(frame).toContain("hello");
  });

  it("renders read_file results as a Read N lines summary", () => {
    const chunk: Chunk = {
      kind: "tool-execution",
      toolCallId: "tool-read",
      toolName: "read_file",
      toolArgs: { file_path: "/tmp/example.ts" },
      status: "success",
      output: "line one\nline two\nline three",
    };

    const { lastFrame } = render(<AssistantToolUseMessage chunk={chunk} />);
    const frame = lastFrame() ?? "";

    expect(frame).toContain("Read(");
    expect(frame).toContain("Read 3 lines");
  });

  it("renders running tool as Running… without output", () => {
    const chunk: Chunk = {
      kind: "tool-execution",
      toolCallId: "tool-running",
      toolName: "execute",
      toolArgs: { command: "sleep 1" },
      status: "running",
    };

    const { lastFrame } = render(<AssistantToolUseMessage chunk={chunk} />);
    const frame = lastFrame() ?? "";

    expect(frame).toContain("Bash(");
    expect(frame).toContain("Running…");
  });
});
