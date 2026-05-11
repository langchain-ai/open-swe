import { Text } from "ink";
import { MessageResponse } from "@tui/components/MessageResponse.js";
import { themeColor } from "@tui/theme.js";
import { argString } from "./utils.js";
import type { ToolUI } from "./types.js";

const MAX_COMMAND_DISPLAY_LINES = 2;
const MAX_COMMAND_DISPLAY_CHARS = 160;

const truncateCommand = (command: string): string => {
  const lines = command.split("\n");
  let truncated = command;
  if (lines.length > MAX_COMMAND_DISPLAY_LINES) {
    truncated = lines.slice(0, MAX_COMMAND_DISPLAY_LINES).join("\n");
  }
  if (truncated.length > MAX_COMMAND_DISPLAY_CHARS) {
    truncated = truncated.slice(0, MAX_COMMAND_DISPLAY_CHARS);
  }
  return truncated.length < command.length
    ? truncated.replace(/\s+$/u, "") + "…"
    : command;
};

const MAX_OUTPUT_LINES = 12;

const renderOutput = (output: string, color?: string) => {
  const trimmed = output.trim();
  if (!trimmed) return null;
  const lines = trimmed.split("\n");
  const shown = lines.slice(0, MAX_OUTPUT_LINES);
  const overflow = lines.length - shown.length;
  return (
    <MessageResponse>
      {shown.map((line, idx) => (
        <Text key={idx} color={color ?? themeColor("inactive")}>
          {line || " "}
        </Text>
      ))}
      {overflow > 0 ? (
        <Text color={themeColor("subtle")}>… +{overflow} more lines</Text>
      ) : null}
    </MessageResponse>
  );
};

export const BashTool: ToolUI = {
  names: ["execute", "execute_shell_command", "shell", "bash"],
  userFacingName: () => "Bash",
  renderToolUseMessage: (args, { verbose }) => {
    const command = argString(args, "command") || argString(args, "cmd");
    if (!command) return null;
    return verbose ? command : truncateCommand(command);
  },
  renderToolResultMessage: (output) => renderOutput(output),
  renderToolErrorMessage: (output) => renderOutput(output, themeColor("error")),
};
