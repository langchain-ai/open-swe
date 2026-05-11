import { Box, Text } from "ink";
import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { themeColor } from "@tui/theme.js";
import { RESPONSE_BAR } from "@tui/figures.js";

type Props = {
  children: ReactNode;
  height?: number;
};

const MessageResponseContext = createContext(false);

export const MessageResponse = ({ children, height }: Props) => {
  const isNested = useContext(MessageResponseContext);
  if (isNested) return <>{children}</>;

  return (
    <MessageResponseContext.Provider value={true}>
      <Box flexDirection="row" height={height} overflow="hidden">
        <Box flexShrink={0}>
          <Text color={themeColor("inactive")}>
            {"  "}
            {RESPONSE_BAR}
            {"  "}
          </Text>
        </Box>
        <Box flexShrink={1} flexGrow={1} flexDirection="column">
          {children}
        </Box>
      </Box>
    </MessageResponseContext.Provider>
  );
};
