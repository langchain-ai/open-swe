import chalk from "chalk";
import React from "react";
import { useTextInput } from "@tui/hooks/useTextInput.js";
import type { BaseTextInputProps } from "@types";
import { BaseTextInput } from "./BaseTextInput.js";

export type TextInputProps = BaseTextInputProps;

/**
 * Top-level text input component. Wires the cursor-state hook
 * (`useTextInput`) to the rendering / paste-handling layer (`BaseTextInput`).
 *
 * No theme integration here on purpose — Coda's theme is applied by the
 * surrounding `<Box borderColor={…}>`, and this component just renders a
 * single line of `<Text>` plus the cursor.
 */
export default function TextInput(props: TextInputProps): React.ReactNode {
  const invert = props.showCursor ? chalk.inverse : (text: string) => text;

  const textInputState = useTextInput({
    value: props.value,
    onChange: props.onChange,
    onSubmit: props.onSubmit,
    onExit: props.onExit,
    onExitMessage: props.onExitMessage,
    onHistoryReset: props.onHistoryReset,
    onHistoryUp: props.onHistoryUp,
    onHistoryDown: props.onHistoryDown,
    onClearInput: props.onClearInput,
    focus: props.focus,
    mask: props.mask,
    multiline: props.multiline,
    cursorChar: props.showCursor ? " " : "",
    highlightPastedText: props.highlightPastedText,
    invert,
    themeText: (text) => text,
    columns: props.columns,
    maxVisibleLines: props.maxVisibleLines,
    disableCursorMovementForUpDownKeys:
      props.disableCursorMovementForUpDownKeys,
    disableEscapeDoublePress: props.disableEscapeDoublePress,
    externalOffset: props.cursorOffset,
    onOffsetChange: props.onChangeCursorOffset,
    inputFilter: props.inputFilter,
  });

  return (
    <BaseTextInput inputState={textInputState} invert={invert} {...props} />
  );
}
