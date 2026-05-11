import { render } from "ink-testing-library";
import { Message } from "./Message.js";
import type { Message as MessageType } from "@types";

describe("Message", () => {
  it("renders a user prompt without a leading bullet", () => {
    const message: MessageType = {
      id: "1",
      author: "user",
      chunks: [{ kind: "text", text: "How do I install bun?" }],
    };

    const { lastFrame } = render(<Message message={message} />);
    const frame = lastFrame() ?? "";

    expect(frame).toContain("How do I install bun?");
    expect(frame).toContain("> ");
    expect(frame).not.toMatch(/^[●⏺]/m);
  });

  it("shows the leading dot only on the first assistant text chunk", () => {
    const message: MessageType = {
      id: "1",
      author: "agent",
      chunks: [
        { kind: "text", text: "First paragraph." },
        { kind: "text", text: "Second paragraph." },
      ],
    };

    const { lastFrame } = render(<Message message={message} />);
    const frame = lastFrame() ?? "";

    const dotMatches = frame.match(/[●⏺]/g) ?? [];
    expect(dotMatches.length).toBe(1);
    expect(frame).toContain("First paragraph.");
    expect(frame).toContain("Second paragraph.");
  });

  it("renders a tool execution under a separate AssistantToolUseMessage", () => {
    const message: MessageType = {
      id: "1",
      author: "system",
      chunks: [
        {
          kind: "tool-execution",
          toolCallId: "t1",
          toolName: "execute",
          toolArgs: { command: "ls" },
          status: "success",
          output: "src\nREADME.md",
        },
      ],
    };

    const { lastFrame } = render(<Message message={message} />);
    const frame = lastFrame() ?? "";

    expect(frame).toContain("Bash(");
    expect(frame).toContain("ls");
    expect(frame).toContain("⎿");
    expect(frame).toContain("src");
    expect(frame).toContain("README.md");
  });

  it("uses the structured Read summary even when no diff is parseable", () => {
    const message: MessageType = {
      id: "1",
      author: "system",
      chunks: [
        {
          kind: "tool-execution",
          toolCallId: "t1",
          toolName: "read_file",
          toolArgs: { file_path: "/tmp/sample.txt" },
          status: "success",
          output: "alpha\nbeta\ngamma\ndelta",
        },
      ],
    };

    const { lastFrame } = render(<Message message={message} />);
    const frame = lastFrame() ?? "";

    expect(frame).toContain("Read(");
    expect(frame).toContain("Read 4 lines");
  });
});
