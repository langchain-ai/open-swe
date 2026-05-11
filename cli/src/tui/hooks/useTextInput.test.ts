import { act, renderHook } from "@testing-library/react";
import type { Key } from "ink";
import { describe, expect, it, vi } from "vitest";
import { useTextInput } from "./useTextInput.js";

function makeKey(overrides: Partial<Key> = {}): Key {
  return overrides as Key;
}

function renderTextInput(value: string, externalOffset = value.length) {
  const onChange = vi.fn();
  const onOffsetChange = vi.fn();

  const result = renderHook(() =>
    useTextInput({
      value,
      onChange,
      cursorChar: " ",
      invert: (text) => text,
      themeText: (text) => text,
      columns: 80,
      externalOffset,
      onOffsetChange,
    }),
  );

  return { ...result, onChange, onOffsetChange };
}

describe("useTextInput", () => {
  it("treats raw DEL as backspace even when Ink labels it delete", () => {
    const { result, onChange, onOffsetChange } = renderTextInput("hello");

    act(() => {
      result.current.onInput("\x7f", makeKey({ delete: true }));
    });

    expect(onChange).toHaveBeenCalledWith("hell");
    expect(onOffsetChange).toHaveBeenCalledWith(4);
  });

  it("treats plain Ink delete as backspace because Ink strips the DEL byte", () => {
    const { result, onChange, onOffsetChange } = renderTextInput("hello");

    act(() => {
      result.current.onInput("", makeKey({ delete: true }));
    });

    expect(onChange).toHaveBeenCalledWith("hell");
    expect(onOffsetChange).toHaveBeenCalledWith(4);
  });
});
