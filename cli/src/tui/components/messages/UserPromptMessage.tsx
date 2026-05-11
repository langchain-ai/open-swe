import { Box, Text } from "ink";
import { themeColor } from "@tui/theme.js";

type Props = {
  text: string;
  timestamp?: string;
};

const MAX_DISPLAY_CHARS = 10_000;
const TRUNCATE_HEAD_CHARS = 2_500;
const TRUNCATE_TAIL_CHARS = 2_500;

const truncate = (text: string): string => {
  if (text.length <= MAX_DISPLAY_CHARS) return text;
  const head = text.slice(0, TRUNCATE_HEAD_CHARS);
  const tail = text.slice(-TRUNCATE_TAIL_CHARS);
  const total = (text.match(/\n/g) ?? []).length;
  const tailNewlines = (tail.match(/\n/g) ?? []).length;
  const headNewlines = (head.match(/\n/g) ?? []).length;
  const hidden = Math.max(0, total - headNewlines - tailNewlines);
  return `${head}\n… +${hidden} lines …\n${tail}`;
};

/**
 * Mirrors the reference TUI's user prompt: a tinted block with no leading
 * bullet. The background color provides the visual anchor instead.
 */
export const UserPromptMessage = ({ text, timestamp }: Props) => {
  if (!text.trim()) return null;
  const display = truncate(text);
  const userBg = themeColor("userMessageBg");
  const subtle = themeColor("subtle");

  return (
    <Box
      flexDirection="column"
      marginTop={1}
      paddingX={1}
      width="100%"
      backgroundColor={userBg}
    >
      {display.split("\n").map((line, idx) => (
        <Text key={idx}>
          <Text color={subtle}>{idx === 0 ? "> " : "  "}</Text>
          {line}
          {idx === 0 && timestamp ? (
            <Text color={subtle}> ({timestamp})</Text>
          ) : null}
        </Text>
      ))}
    </Box>
  );
};
