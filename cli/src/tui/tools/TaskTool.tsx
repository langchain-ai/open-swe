import { Text } from "ink";
import { MessageResponse } from "@tui/components/MessageResponse.js";
import { themeColor } from "@tui/theme.js";
import { argString } from "./utils.js";
import type { ToolUI } from "./types.js";

export const TaskTool: ToolUI = {
  names: ["task"],
  userFacingName: () => "Task",
  renderToolUseMessage: (args) => {
    const description = argString(args, "description");
    const subagent = argString(args, "subagent_type");
    if (description && subagent) return `${subagent} · ${description}`;
    return description || subagent || "subagent";
  },
  renderToolResultMessage: (output) => {
    if (!output) return null;
    const summary = output.split("\n").slice(0, 4).join("\n");
    return (
      <MessageResponse>
        <Text color={themeColor("inactive")}>{summary}</Text>
      </MessageResponse>
    );
  },
};
