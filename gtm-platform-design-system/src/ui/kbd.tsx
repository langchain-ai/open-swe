/*
 * Kbd, on CORE 14. The keyboard hint chip.
 *
 * The boards draw these in exactly two places, and both are hints rather than
 * controls: the composer's send affordance and the search field's shortcut chip
 * (the dock's 32px search carries a stand-alone shortcut block beside it). So it
 * is sized as a chip, not as a control -- `h-badge` (20px), `rounded-badge`,
 * `bg-muted` and a `line` hairline -- and it is the same 20px object a Badge is,
 * because a shortcut chip and a status chip standing in the same row that
 * measured differently would be the tell.
 *
 * `min-w-badge` squares up a single key: `K` and `/` come out as 20x20 tiles,
 * while a word key like `Esc` grows on its padding instead of stretching the
 * height. Type is mono at the meta rung, which is where the brand book puts
 * every piece of metadata, and `text-ink-subtle` because a hint that competes
 * with the label it hints at is a hint nobody asked for.
 *
 * It renders a real `<kbd>` element. That is not decoration: it is the one thing
 * that tells a screen reader this is a key to press and not a two-letter word,
 * and it is free.
 *
 * One key only. A chord is `Shortcut` (`keys={["mod", "K"]}`), which lays the
 * chips out and maps `mod` to ⌘ or Ctrl. Do not put "⌘K" in one Kbd.
 */

import { cn } from "./cn";

function Kbd({ className, ...props }: React.ComponentProps<"kbd">) {
  return (
    <kbd
      data-slot="kbd"
      className={cn(
        "inline-flex h-badge min-w-badge shrink-0 items-center justify-center rounded-badge border border-line bg-muted px-1.5 font-mono text-meta text-ink-subtle",
        className
      )}
      {...props}
    />
  );
}

export { Kbd };
