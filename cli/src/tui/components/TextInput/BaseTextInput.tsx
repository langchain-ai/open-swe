import type React from "react";
import { Box, Text, useInput } from "ink";
import { renderPlaceholder } from "@tui/hooks/renderPlaceholder.js";
import { usePasteHandler } from "@tui/hooks/usePasteHandler.js";
import type { BaseInputState, BaseTextInputProps } from "@types";

type BaseTextInputComponentProps = BaseTextInputProps & {
  inputState: BaseInputState;
  invert?: (text: string) => string;
};

/**
 * Renders the live text/placeholder and routes keystrokes through the paste
 * handler before they reach the cursor logic in `useTextInput`.
 *
 * This component intentionally stays passive: it doesn't own cursor state,
 * doesn't know about commands or images — it just glues `useInput`,
 * `usePasteHandler`, and `useTextInput` together.
 */
export function BaseTextInput(
  props: BaseTextInputComponentProps,
): React.ReactNode {
  const { inputState, invert, ...rest } = props;
  const { onInput, renderedValue } = inputState;

  const { wrappedOnInput, isPasting } = usePasteHandler({
    onPaste: rest.onPaste,
    onInput: (input, key) => {
      // Suppress an Enter that arrives during paste — bracketed paste sometimes
      // coalesces a trailing \r that would otherwise submit the in-progress paste.
      if (isPasting && key.return) return;
      onInput(input, key);
    },
    onImagePaste: rest.onImagePaste,
  });

  const { showPlaceholder, renderedPlaceholder } = renderPlaceholder({
    placeholder: rest.placeholder,
    value: rest.value,
    showCursor: rest.showCursor,
    focus: rest.focus,
    invert,
  });

  useInput(wrappedOnInput, { isActive: rest.focus });

  // Show the argument hint only when the user has typed a slash command
  // without arguments yet. The hint is dimmed and never inverted.
  const commandWithoutArgs =
    (!!rest.value && rest.value.trim().indexOf(" ") === -1) ||
    (!!rest.value && rest.value.endsWith(" "));
  const showArgumentHint = Boolean(
    rest.argumentHint &&
    rest.value &&
    commandWithoutArgs &&
    rest.value.startsWith("/"),
  );

  return (
    <Box>
      <Text wrap="truncate-end" dimColor={rest.dimColor}>
        {showPlaceholder && renderedPlaceholder
          ? renderedPlaceholder
          : renderedValue}
        {showArgumentHint && (
          <Text dimColor>
            {rest.value?.endsWith(" ") ? "" : " "}
            {rest.argumentHint}
          </Text>
        )}
      </Text>
    </Box>
  );
}
