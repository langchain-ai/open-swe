import { isKnownSafeCommand } from "../command-evaluation.js";
import type { ToolCall } from "@langchain/core/messages/tool";

describe("isKnownSafeCommand", () => {
  const baseToolCall = (command: string[]): ToolCall => ({
    id: "tool-call",
    name: "shell",
    args: {
      command,
    },
  });

  it("treats chmod +x commands as known safe", () => {
    const toolCall = baseToolCall(["chmod", "+x", "hello_world.py"]);
    expect(isKnownSafeCommand(toolCall)).toBe(true);
  });

  it("treats sudo chmod numeric mode commands as known safe", () => {
    const toolCall = baseToolCall(["sudo", "chmod", "755", "scripts/run.sh"]);
    expect(isKnownSafeCommand(toolCall)).toBe(true);
  });

  it("returns false for non-chmod shell commands", () => {
    const toolCall = baseToolCall(["rm", "-rf", "/"]);
    expect(isKnownSafeCommand(toolCall)).toBe(false);
  });

  it("returns false for chmod commands missing a mode", () => {
    const toolCall = baseToolCall(["chmod", "+", "file.txt"]);
    expect(isKnownSafeCommand(toolCall)).toBe(false);
  });
});
