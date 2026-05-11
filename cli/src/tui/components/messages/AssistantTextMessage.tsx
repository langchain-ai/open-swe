import { Box, Text } from "ink";
import { Markdown } from "@tui/components/Markdown.js";
import { themeColor } from "@tui/theme.js";
import { BLACK_CIRCLE } from "@tui/figures.js";

type Props = {
  text: string;
  shouldShowDot?: boolean;
};

/**
 * Plain assistant text rendered as markdown, optionally prefixed with the
 * leading bullet that anchors a "turn" of the conversation. Continuations
 * within the same turn pass `shouldShowDot={false}` so they hang under the
 * first bullet without an extra blank line above them.
 */
export const AssistantTextMessage = ({ text, shouldShowDot = true }: Props) => {
  if (!text.trim()) return null;
  return (
    <Box flexDirection="row" marginTop={shouldShowDot ? 1 : 0} width="100%">
      <Box minWidth={2} flexShrink={0}>
        {shouldShowDot ? (
          <Text color={themeColor("text")}>{BLACK_CIRCLE}</Text>
        ) : null}
      </Box>
      <Box flexDirection="column" flexGrow={1}>
        <Markdown>{text}</Markdown>
      </Box>
    </Box>
  );
};
