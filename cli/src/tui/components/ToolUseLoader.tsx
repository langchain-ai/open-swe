import { Box, Text } from "ink";
import { useStore } from "@app/store.js";
import { themeColor } from "@tui/theme.js";
import { BLACK_CIRCLE } from "@tui/figures.js";

type Props = {
  isError: boolean;
  isUnresolved: boolean;
  shouldAnimate?: boolean;
};

/**
 * Status dot for tool calls. Mirrors the reference TUI:
 * - Running: blinks BLACK_CIRCLE in default text color (dim).
 * - Success: solid green BLACK_CIRCLE.
 * - Error: solid red BLACK_CIRCLE.
 *
 * The blink is driven by the global `tick` so dozens of concurrent tool calls
 * render cheaply side-by-side without per-instance timers.
 */
export const ToolUseLoader = ({
  isError,
  isUnresolved,
  shouldAnimate = true,
}: Props) => {
  const blink = useStore((s) => s.blink);
  const showDot = !shouldAnimate || blink || isError || !isUnresolved;
  const color = isUnresolved
    ? themeColor("inactive")
    : isError
      ? themeColor("error")
      : themeColor("success");

  return (
    <Box minWidth={2}>
      <Text color={color}>{showDot ? BLACK_CIRCLE : " "}</Text>
    </Box>
  );
};
