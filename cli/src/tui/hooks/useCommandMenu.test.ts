import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useCommandMenu } from "./useCommandMenu.js";

describe("useCommandMenu", () => {
  it("closes when the slash command token is deleted", () => {
    const { result } = renderHook(() => useCommandMenu());

    act(() => result.current.filterFromQuery("/"));
    expect(result.current.showCommandMenu).toBe(true);
    expect(result.current.filteredCommands.length).toBeGreaterThan(0);

    act(() => result.current.filterFromQuery(""));
    expect(result.current.showCommandMenu).toBe(false);
    expect(result.current.commandSelectionIndex).toBe(0);
    expect(result.current.filteredCommands).toEqual([]);
  });
});
