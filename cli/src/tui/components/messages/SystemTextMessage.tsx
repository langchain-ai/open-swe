import { Box, Text } from "ink";
import { themeColor } from "@tui/theme.js";
import { BULLET } from "@tui/figures.js";

type Props = {
  text: string;
};

/**
 * Subtle, dim system note. Used for slash-command echoes, session boundaries,
 * and similar metadata that shouldn't draw the eye.
 */
export const SystemTextMessage = ({ text }: Props) => {
  if (!text.trim()) return null;
  const subtle = themeColor("subtle");
  const inactive = themeColor("inactive");
  return (
    <Box flexDirection="row" marginTop={1}>
      <Box minWidth={2}>
        <Text color={subtle}>{BULLET}</Text>
      </Box>
      <Box flexDirection="column" flexGrow={1}>
        {text.split("\n").map((line, idx) => (
          <Text key={idx} color={inactive}>
            {line || " "}
          </Text>
        ))}
      </Box>
    </Box>
  );
};
