"use client";

/*
 * Shortcut, the chord. Kbd is one key; this is two or more chips in a row.
 *
 * `mod` is the platform modifier: ⌘ on Apple, Ctrl everywhere else. That
 * policy lives here so Kbd never has to guess, and so Command, menus, and
 * search fields draw the same chord. `aria-keyshortcuts` is written on the
 * group so the hint is announced, not only painted.
 */

import { Inline } from "./box";
import { cn } from "./cn";
import { Kbd } from "./kbd";

type ShortcutNamedKey =
  | "mod"
  | "meta"
  | "ctrl"
  | "alt"
  | "shift"
  | "enter"
  | "esc"
  | "tab"
  | "up"
  | "down";

type ShortcutKey = ShortcutNamedKey | (string & {});

const SHORTCUT_RULES: readonly string[] = [
  "Kbd is one key. Shortcut is the chord: a row of Kbd chips, never a string like ⌘K inside one chip.",
  "`mod` is the platform modifier (⌘ on Apple, Ctrl elsewhere). Do not hard-code ⌘ in product chrome.",
  "The group carries `aria-keyshortcuts`. The chips stay visual.",
];

function prefersAppleModifier(): boolean {
  if (typeof navigator === "undefined") return true;
  return /Mac|iPhone|iPad|iPod/.test(navigator.userAgent);
}

function shortcutKeyLabel(key: ShortcutKey, apple: boolean): string {
  switch (key) {
    case "mod":
      return apple ? "⌘" : "Ctrl";
    case "meta":
      return "⌘";
    case "ctrl":
      return "Ctrl";
    case "alt":
      return apple ? "⌥" : "Alt";
    case "shift":
      return "Shift";
    case "enter":
      return "↵";
    case "esc":
      return "Esc";
    case "tab":
      return "Tab";
    case "up":
      return "↑";
    case "down":
      return "↓";
    default:
      return key.length === 1 ? key.toUpperCase() : key;
  }
}

function shortcutAriaToken(key: ShortcutKey): string {
  switch (key) {
    case "mod":
    case "meta":
      return "Meta";
    case "ctrl":
      return "Control";
    case "alt":
      return "Alt";
    case "shift":
      return "Shift";
    case "enter":
      return "Enter";
    case "esc":
      return "Escape";
    case "tab":
      return "Tab";
    case "up":
      return "ArrowUp";
    case "down":
      return "ArrowDown";
    default:
      return key.length === 1 ? key.toUpperCase() : key;
  }
}

function shortcutAriaKeyshortcuts(keys: readonly ShortcutKey[]): string {
  const tokens = keys.map(shortcutAriaToken);
  const chord = tokens.join("+");
  if (keys.includes("mod")) {
    const control = keys
      .map((key) => (key === "mod" ? "Control" : shortcutAriaToken(key)))
      .join("+");
    return `${chord} ${control}`;
  }
  return chord;
}

function parseShortcutString(value: string): ShortcutKey[] {
  if (value.startsWith("⌘") && value.length > 1) {
    return ["mod", value.slice(1)];
  }
  if (value.startsWith("Ctrl+") && value.length > 5) {
    return ["ctrl", value.slice(5)];
  }
  return [value];
}

interface ShortcutProps {
  keys: readonly ShortcutKey[];
  /** Force Apple (⌘) or not. Omit to detect. Tests pass this. */
  apple?: boolean;
  className?: string;
}

function Shortcut({ apple, className, keys }: ShortcutProps) {
  const useApple = apple ?? prefersAppleModifier();
  return (
    <Inline
      data-slot="shortcut"
      gap="xs"
      align="center"
      aria-keyshortcuts={shortcutAriaKeyshortcuts(keys)}
      className={cn("shrink-0", className)}
    >
      {keys.map((key, index) => (
        <Kbd key={`${shortcutKeyLabel(key, useApple)}-${index}`}>
          {shortcutKeyLabel(key, useApple)}
        </Kbd>
      ))}
    </Inline>
  );
}

export {
  parseShortcutString,
  SHORTCUT_RULES,
  Shortcut,
  shortcutAriaKeyshortcuts,
  shortcutKeyLabel,
};
export type { ShortcutKey, ShortcutProps };
