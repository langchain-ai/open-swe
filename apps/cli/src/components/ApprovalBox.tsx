import React from "react";
import { Box, Text } from "ink";

interface ApprovalBoxProps {
  interrupt: {
    command: string;
    args: Record<string, any>;
    id: string;
  };
}

export const ApprovalBox: React.FC<ApprovalBoxProps> = ({ interrupt }) => {
  return (
    <Box paddingX={2} paddingY={1}>
      <Box
        borderStyle="round"
        borderColor="white"
        paddingX={3}
        paddingY={1}
        flexDirection="column"
      >
        <Box marginBottom={1}>
          <Text bold>Command Approval Required</Text>
        </Box>
        <Box marginBottom={1}>
          <Text>
            Command: <Text bold>{interrupt.command}</Text>
          </Text>
        </Box>
        <Box>
          <Text>
            Arguments:{" "}
            <Text bold>
              {(() => {
                if (interrupt.args.file_path || interrupt.args.path) {
                  const filePath =
                    interrupt.args.file_path || interrupt.args.path;
                  if (interrupt.args.content) {
                    const lines = interrupt.args.content.split("\n").length;
                    return `${filePath} (${lines} lines)`;
                  }
                  return filePath;
                }
                const argsStr = Object.values(interrupt.args).join(" ");
                return argsStr.length > 100
                  ? argsStr.substring(0, 100) + "..."
                  : argsStr;
              })()}
            </Text>
          </Text>
        </Box>
        <Box marginTop={1}>
          <Text>
            Type <Text bold>yes</Text> to approve, <Text bold>no</Text> to
            reject, or enter a custom command
          </Text>
        </Box>
      </Box>
    </Box>
  );
};
