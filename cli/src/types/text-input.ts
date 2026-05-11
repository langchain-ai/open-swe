import type { Key } from "ink";

/**
 * Shared types for the text-input system.
 *
 * Trimmed to the features Coda actually exercises (no vim mode, no inline
 * ghost text, no accessibility overrides). Re-add fields cautiously: any new
 * prop must be threaded through `useTextInput` and `BaseTextInput`.
 */

export type BaseTextInputProps = {
  /** Listen to user's input. Useful when there are multiple input components. */
  readonly focus?: boolean;

  /** Replace all chars and mask the value. Useful for password inputs. */
  readonly mask?: string;

  /** Whether to show cursor and allow navigation inside text input with arrow keys. */
  readonly showCursor?: boolean;

  /** Highlight text inserted via paste. */
  readonly highlightPastedText?: boolean;

  /** Allow multi-line input via line ending with backslash + Enter, or Shift/Alt+Enter. */
  readonly multiline?: boolean;

  /** Text to display when `value` is empty. */
  readonly placeholder?: string;

  /** Number of columns to wrap text at. */
  readonly columns: number;

  /**
   * Maximum visible lines for the input viewport. When the wrapped input
   * exceeds this many lines, only lines around the cursor are rendered.
   */
  readonly maxVisibleLines?: number;

  /** Optional argument hint rendered after a slash command (e.g. `/git <message>`). */
  readonly argumentHint?: string;

  /** Render the text with dim color. */
  readonly dimColor?: boolean;

  /** Skip the text-level double-press escape handler. */
  readonly disableEscapeDoublePress?: boolean;

  /** Disable cursor movement for up/down arrow keys (e.g. when a menu owns nav). */
  readonly disableCursorMovementForUpDownKeys?: boolean;

  /** Current text value. */
  readonly value: string;
  /** Called when the value changes (typing, paste, image insert, etc.). */
  readonly onChange: (value: string) => void;

  /** The offset of the cursor within the text. */
  readonly cursorOffset: number;
  /** Called when the cursor offset changes. */
  readonly onChangeCursorOffset: (offset: number) => void;

  /** Called when Enter is pressed (single-line, or non-newline-Enter in multiline). */
  readonly onSubmit?: (value: string) => void;
  /** Called when Ctrl+C is pressed twice on an empty input (or Ctrl+D on empty). */
  readonly onExit?: () => void;
  /** Called to surface the "press X again to exit" hint. */
  readonly onExitMessage?: (show: boolean, key?: string) => void;

  /** Called when the user presses Up at the top of the input. */
  readonly onHistoryUp?: () => void;
  /** Called when the user presses Down at the bottom of the input. */
  readonly onHistoryDown?: () => void;
  /** Called when history navigation should reset (e.g. after typing). */
  readonly onHistoryReset?: () => void;
  /** Called when input is cleared (e.g. double-escape). */
  readonly onClearInput?: () => void;

  /** Called with the pasted text on a large paste. Receives the raw bracketed-paste content. */
  readonly onPaste?: (text: string) => void;

  /** Called when an image is detected (drag-and-drop or paste). */
  readonly onImagePaste?: (
    base64Image: string,
    mediaType?: string,
    filename?: string,
    sourcePath?: string,
  ) => void;

  /** Filter applied to raw input before key routing. Return '' to drop. */
  readonly inputFilter?: (input: string, key: Key) => string;
};

/** Common rendering state for text inputs. */
export type BaseInputState = {
  onInput: (input: string, key: Key) => void;
  renderedValue: string;
  offset: number;
  setOffset: (offset: number) => void;
  /** Cursor line (0-indexed) within the rendered text, accounting for wrapping. */
  cursorLine: number;
  /** Cursor column (display-width) within the current line. */
  cursorColumn: number;
  /** Character offset where the viewport starts. */
  viewportCharOffset: number;
  /** Character offset where the viewport ends. */
  viewportCharEnd: number;
};

export type TextInputState = BaseInputState;
