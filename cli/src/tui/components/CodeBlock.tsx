import { Box, Text } from "ink";
import { themeColor } from "@tui/theme.js";
import type { CodeBlockProps } from "@types";

export const CodeBlock = ({ lines }: CodeBlockProps) => {
  const subtle = themeColor("subtle");
  const inactive = themeColor("inactive");
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      paddingX={1}
      borderColor={subtle}
    >
      {lines.map((line, idx) => (
        <Text key={idx} color={inactive}>
          {line}
        </Text>
      ))}
    </Box>
  );
};
