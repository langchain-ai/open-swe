import { Text } from "ink";
import { MessageResponse } from "@tui/components/MessageResponse.js";
import { themeColor } from "@tui/theme.js";
import { argString } from "./utils.js";
import type { ToolUI } from "./types.js";

export const GlobTool: ToolUI = {
  names: ["glob"],
  userFacingName: () => "Glob",
  renderToolUseMessage: (args) => {
    const pattern = argString(args, "pattern");
    const path = argString(args, "path");
    if (!pattern) return null;
    if (path && path !== "/") return `${pattern} in ${path}`;
    return pattern;
  },
  renderToolResultMessage: (output) => {
    if (!output) return null;
    const trimmed = output.trim();
    if (trimmed.startsWith("No files")) {
      return (
        <MessageResponse height={1}>
          <Text color={themeColor("inactive")}>No matches</Text>
        </MessageResponse>
      );
    }
    const count = trimmed.split("\n").filter(Boolean).length;
    return (
      <MessageResponse height={1}>
        <Text>
          Found <Text bold>{count}</Text> {count === 1 ? "file" : "files"}
        </Text>
      </MessageResponse>
    );
  },
};
