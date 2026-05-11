import type { Key } from "ink";
import stripAnsi from "strip-ansi";
import {
  Cursor,
  getLastKill,
  pushToKillRing,
  recordYank,
  resetKillAccumulation,
  resetYankState,
  updateYankLength,
  yankPop,
} from "@lib/text-input/cursor.js";
import type { TextInputState } from "@types";
import { useDoublePress } from "@tui/hooks/useDoublePress.js";

type MaybeCursor = void | Cursor;
type InputHandler = (input: string) => MaybeCursor;
type InputMapper = (input: string) => MaybeCursor;
const NOOP_HANDLER: InputHandler = () => {};

function mapInput(input_map: Array<[string, InputHandler]>): InputMapper {
  const map = new Map(input_map);
  return function (input: string): MaybeCursor {
    return (map.get(input) ?? NOOP_HANDLER)(input);
  };
}

export type UseTextInputProps = {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: (value: string) => void;
  onExit?: () => void;
  onExitMessage?: (show: boolean, key?: string) => void;
  onHistoryUp?: () => void;
  onHistoryDown?: () => void;
  onHistoryReset?: () => void;
  onClearInput?: () => void;
  focus?: boolean;
  mask?: string;
  multiline?: boolean;
  cursorChar: string;
  highlightPastedText?: boolean;
  invert: (text: string) => string;
  themeText: (text: string) => string;
  columns: number;
  disableCursorMovementForUpDownKeys?: boolean;
  disableEscapeDoublePress?: boolean;
  maxVisibleLines?: number;
  externalOffset: number;
  onOffsetChange: (offset: number) => void;
  inputFilter?: (input: string, key: Key) => string;
};

/**
 * Core text-editor hook. Owns cursor state, kill-ring / yank, history-edge
 * navigation, and Emacs-style keybindings. The actual paste handling lives
 * in `usePasteHandler`, which wraps this hook's `onInput`.
 */
export function useTextInput({
  value: originalValue,
  onChange,
  onSubmit,
  onExit,
  onExitMessage,
  onHistoryUp,
  onHistoryDown,
  onHistoryReset,
  onClearInput,
  mask = "",
  multiline = false,
  cursorChar,
  invert,
  columns,
  disableCursorMovementForUpDownKeys = false,
  disableEscapeDoublePress = false,
  maxVisibleLines,
  externalOffset,
  onOffsetChange,
  inputFilter,
}: UseTextInputProps): TextInputState {
  const offset = externalOffset;
  const setOffset = onOffsetChange;
  const cursor = Cursor.fromText(originalValue, columns, offset);

  const handleCtrlC = useDoublePress(
    (show) => {
      onExitMessage?.(show, "Ctrl-C");
    },
    () => onExit?.(),
    () => {
      if (originalValue) {
        onChange("");
        setOffset(0);
        onHistoryReset?.();
      }
    },
  );

  // Double-press Esc clears the input.
  const handleEscape = useDoublePress(
    () => {
      // First press: caller can render a hint via showExitMessage if desired.
    },
    () => {
      onClearInput?.();
      if (originalValue) {
        onChange("");
        setOffset(0);
        onHistoryReset?.();
      }
    },
  );

  const handleEmptyCtrlD = useDoublePress(
    (show) => {
      if (originalValue !== "") return;
      onExitMessage?.(show, "Ctrl-D");
    },
    () => {
      if (originalValue !== "") return;
      onExit?.();
    },
  );

  function handleCtrlD(): MaybeCursor {
    if (cursor.text === "") {
      handleEmptyCtrlD();
      return cursor;
    }
    return cursor.del();
  }

  function killToLineEnd(): Cursor {
    const { cursor: newCursor, killed } = cursor.deleteToLineEnd();
    pushToKillRing(killed, "append");
    return newCursor;
  }

  function killToLineStart(): Cursor {
    const { cursor: newCursor, killed } = cursor.deleteToLineStart();
    pushToKillRing(killed, "prepend");
    return newCursor;
  }

  function killWordBefore(): Cursor {
    const { cursor: newCursor, killed } = cursor.deleteWordBefore();
    pushToKillRing(killed, "prepend");
    return newCursor;
  }

  function yank(): Cursor {
    const text = getLastKill();
    if (text.length > 0) {
      const startOffset = cursor.offset;
      const newCursor = cursor.insert(text);
      recordYank(startOffset, text.length);
      return newCursor;
    }
    return cursor;
  }

  function handleYankPop(): Cursor {
    const popResult = yankPop();
    if (!popResult) return cursor;
    const { text, start, length } = popResult;
    const before = cursor.text.slice(0, start);
    const after = cursor.text.slice(start + length);
    const newText = before + text + after;
    const newOffset = start + text.length;
    updateYankLength(text.length);
    return Cursor.fromText(newText, columns, newOffset);
  }

  const handleCtrl = mapInput([
    ["a", () => cursor.startOfLine()],
    ["b", () => cursor.left()],
    ["c", handleCtrlC],
    ["d", handleCtrlD],
    ["e", () => cursor.endOfLine()],
    ["f", () => cursor.right()],
    ["h", () => cursor.deleteTokenBefore() ?? cursor.backspace()],
    ["k", killToLineEnd],
    ["n", () => downOrHistoryDown()],
    ["p", () => upOrHistoryUp()],
    ["u", killToLineStart],
    ["w", killWordBefore],
    ["y", yank],
  ]);

  const handleMeta = mapInput([
    ["b", () => cursor.prevWord()],
    ["f", () => cursor.nextWord()],
    ["d", () => cursor.deleteWordAfter()],
    ["y", handleYankPop],
  ]);

  function handleEnter(key: Key): MaybeCursor {
    if (
      multiline &&
      cursor.offset > 0 &&
      cursor.text[cursor.offset - 1] === "\\"
    ) {
      // Backslash-Enter inserts a newline in multiline mode.
      return cursor.backspace().insert("\n");
    }
    if (key.meta || key.shift) return cursor.insert("\n");
    onSubmit?.(originalValue);
  }

  function upOrHistoryUp(): Cursor {
    if (disableCursorMovementForUpDownKeys) {
      onHistoryUp?.();
      return cursor;
    }
    const cursorUp = cursor.up();
    if (!cursorUp.equals(cursor)) return cursorUp;
    if (multiline) {
      const cursorUpLogical = cursor.upLogicalLine();
      if (!cursorUpLogical.equals(cursor)) return cursorUpLogical;
    }
    onHistoryUp?.();
    return cursor;
  }

  function downOrHistoryDown(): Cursor {
    if (disableCursorMovementForUpDownKeys) {
      onHistoryDown?.();
      return cursor;
    }
    const cursorDown = cursor.down();
    if (!cursorDown.equals(cursor)) return cursorDown;
    if (multiline) {
      const cursorDownLogical = cursor.downLogicalLine();
      if (!cursorDownLogical.equals(cursor)) return cursorDownLogical;
    }
    onHistoryDown?.();
    return cursor;
  }

  function mapKey(key: Key): InputMapper {
    switch (true) {
      case key.escape:
        return () => {
          if (disableEscapeDoublePress) return cursor;
          handleEscape();
          return cursor;
        };
      case key.leftArrow && (key.ctrl || key.meta):
        return () => cursor.prevWord();
      case key.rightArrow && (key.ctrl || key.meta):
        return () => cursor.nextWord();
      case key.backspace:
        return key.meta || key.ctrl
          ? killWordBefore
          : () => cursor.deleteTokenBefore() ?? cursor.backspace();
      case key.delete:
        // Ink 6 maps the DEL byte emitted by common Backspace keys to
        // `delete`, and strips the raw input before this hook sees it. Treat
        // unmodified delete as erase-left so prompt editing matches terminal
        // expectations in Ghostty and other modern emulators.
        return key.meta || key.ctrl
          ? killWordBefore
          : () => cursor.deleteTokenBefore() ?? cursor.backspace();
      case key.ctrl:
        return handleCtrl;
      case key.home:
        return () => cursor.startOfLine();
      case key.end:
        return () => cursor.endOfLine();
      case key.pageDown:
        return () => cursor.endOfLine();
      case key.pageUp:
        return () => cursor.startOfLine();
      case key.return:
        return () => handleEnter(key);
      case key.meta:
        return handleMeta;
      case key.tab:
        return () => cursor;
      case key.upArrow && !key.shift:
        return upOrHistoryUp;
      case key.downArrow && !key.shift:
        return downOrHistoryDown;
      case key.leftArrow:
        return () => cursor.left();
      case key.rightArrow:
        return () => cursor.right();
      default:
        return function (input: string) {
          switch (true) {
            case input === "\x1b[H" || input === "\x1b[1~":
              return cursor.startOfLine();
            case input === "\x1b[F" || input === "\x1b[4~":
              return cursor.endOfLine();
            default: {
              // Strip a single trailing \r (SSH-coalesced Enter), keep embedded
              // \r as \n (multi-line paste from a non-bracketed-paste terminal),
              // and preserve backslash+\r (a stale VS Code Shift+Enter binding).
              const text = stripAnsi(input)
                .replace(/(?<=[^\\\r\n])\r$/, "")
                .replace(/\r/g, "\n");
              return cursor.insert(text);
            }
          }
        };
    }
  }

  function isKillKey(key: Key, input: string): boolean {
    if (key.ctrl && (input === "k" || input === "u" || input === "w"))
      return true;
    if (key.meta && (key.backspace || key.delete)) return true;
    return false;
  }

  function isYankKey(key: Key, input: string): boolean {
    return (key.ctrl || key.meta) && input === "y";
  }

  function onInput(input: string, key: Key): void {
    const filteredInput = inputFilter ? inputFilter(input, key) : input;
    if (filteredInput === "" && input !== "") return;

    // Terminals disagree on whether DEL (\x7f) is "backspace" or "delete".
    // The byte itself means erase-left for prompt editing, so honor it before
    // Ink's key label. Actual forward-delete sends an escape sequence instead.
    if (filteredInput.includes("\x7f")) {
      const delCount = (filteredInput.match(/\x7f/g) || []).length;
      let currentCursor = cursor;
      for (let i = 0; i < delCount; i++) {
        currentCursor =
          currentCursor.deleteTokenBefore() ?? currentCursor.backspace();
      }
      if (!cursor.equals(currentCursor)) {
        if (cursor.text !== currentCursor.text) onChange(currentCursor.text);
        setOffset(currentCursor.offset);
      }
      resetKillAccumulation();
      resetYankState();
      return;
    }

    if (!isKillKey(key, filteredInput)) resetKillAccumulation();
    if (!isYankKey(key, filteredInput)) resetYankState();

    const nextCursor = mapKey(key)(filteredInput);
    if (nextCursor) {
      if (!cursor.equals(nextCursor)) {
        if (cursor.text !== nextCursor.text) onChange(nextCursor.text);
        setOffset(nextCursor.offset);
      }
      // SSH-coalesced Enter: "o\r" arriving as one chunk hits the default
      // handler above (which strips the trailing \r). A single trailing \r
      // with no embedded \r is coalesced Enter; submit after applying the
      // text edit.
      if (
        filteredInput.length > 1 &&
        filteredInput.endsWith("\r") &&
        !filteredInput.slice(0, -1).includes("\r") &&
        filteredInput[filteredInput.length - 2] !== "\\"
      ) {
        onSubmit?.(nextCursor.text);
      }
    }
  }

  const cursorPos = cursor.getPosition();

  return {
    onInput,
    renderedValue: cursor.render(
      cursorChar,
      mask,
      invert,
      undefined,
      maxVisibleLines,
    ),
    offset,
    setOffset,
    cursorLine: cursorPos.line - cursor.getViewportStartLine(maxVisibleLines),
    cursorColumn: cursorPos.column,
    viewportCharOffset: cursor.getViewportCharOffset(maxVisibleLines),
    viewportCharEnd: cursor.getViewportCharEnd(maxVisibleLines),
  };
}
