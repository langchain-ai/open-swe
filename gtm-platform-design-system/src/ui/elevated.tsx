"use client";

/*
 * The elevation primitive.
 *
 * Ported in principle, not in code, from fluid-functionalism's `elevated`
 * (docs/reference/fluid-functionalism/elevated.tsx and its adoption contract).
 * The one idea worth having is that depth is an OFFSET, not a surface: a
 * component says how many steps it sits above whatever it landed on, a context
 * adds that to the substrate it read, and the resolved level is re-provided to
 * everything inside. Nesting then comes out right without anyone thinking about
 * it, which is the whole point -- a menu opened inside a dialog climbs from the
 * dialog's level, and the same menu opened on the page climbs from the page's,
 * with no prop threaded between them and no call site choosing a number.
 *
 * The visuals are re-expressed in CORE 14: the fills are our surface tokens and
 * the shadows are the four rungs in src/styles/elevation.css. When each rung is
 * legal (and when none is) is documented in docs/reference/elevation-rules.md
 * and restated in ELEVATION_RULES below. Nothing here imports anything from the
 * reference.
 *
 * THE FILL LADDER HAS TWO RUNGS, AND THAT IS CORRECT. CORE 14 gives this
 * product exactly two substrates, `canvas` and `panel`, and the source ladder's
 * own light physics does the same thing for the same reason: two colour steps,
 * then flat, with everything above differentiated by shadow alone. In dark the
 * pair is genuinely additive (#191919 -> #212121), which is the step the source
 * builds by hand. So level 0 is canvas, every level above it is panel, and the
 * climb past that is carried by the rung, never by inventing a third fill.
 */

import * as React from "react";
import { mergeProps } from "@base-ui/react/merge-props";
import { useRender } from "@base-ui/react/use-render";

import { cn } from "./cn";

/**
 * Rules for elevation. The religious part, stated once.
 *
 * WHEN TO USE A RUNG (pick by the job, never by taste):
 *
 *   shadow-control     -- a SOLID pressable that sits alone on a substrate
 *                         (primary/secondary button, slider thumb). One whisper.
 *   shadow-raised-hover -- primary button under the pointer ONLY. Secondary,
 *                         outline, ghost, and compact quiet actions do not lift.
 *   shadow-popup       -- an anchored floating surface that left the page
 *                         (popover, dropdown menu, select list, hover card).
 *                         Soft float; same drop stack as raised-hover. Pair with
 *                         ONE edge (`ring-1 ring-line-strong`), never a second.
 *                         Import `POPUP_SURFACE_*` from `popup-surface.ts` —
 *                         do not restate the shell at a call site.
 *   shadow-overlay     -- a modal that owns the page (dialog, sheet). The only
 *                         heavy rung. Pair with one edge (ring or border).
 *
 * WHEN NOT TO USE ANY RUNG:
 *
 *   - Containment: panels, cards, frames, tables, rows, chat bubbles. Surface
 *     plus one hairline. A box that shadows itself claims a push/float
 *     affordance it does not have.
 *   - Outline / bordered pressables: the hairline IS the edge (Button outline,
 *     Select trigger). Border + shadow is two edges.
 *   - Segmented selected states (Tabs, ToggleGroup): the track already frames
 *     the segment; fill contrast does the job.
 *   - Ghost / quiet toolbar actions.
 *   - Stock Tailwind shadows (`shadow-sm` / `md` / `lg`): the four named rungs
 *     are the only legal vocabulary.
 *
 * ONE EDGE. The component paints the edge (`ring-1 ring-line-strong` or a real
 * border). The shadow paints soft separation only -- never a second ring, never
 * an outer hairline inside the shadow stack. That double edge was the "thicker
 * than fluid" reading on popovers and buttons (Amal 2026-08-06).
 *
 * Depth is COMPUTED, never picked. Conventional offsets: +2 anchored popup,
 * +4 modal overlay. A pressable that lifts on hover settles flat on `:active`.
 * The fill ladder stops at `panel`; above it, depth is shadow alone.
 */
const ELEVATION_RULES = [
  "Depth is computed, never picked: declare an offset, let the context resolve the level.",
  "Use shadow-control on a solid pressable that sits alone; shadow-raised-hover on primary hover only.",
  "Use shadow-popup on anchored floats (popover, menu, select list, hover card); shadow-overlay on dialogs and sheets.",
  "Anchored floats import POPUP_SURFACE_DENSE / PADDED / FLUSH / SHELL from popup-surface.ts — never hand-roll border+shadow-popup.",
  "Never shadow containment, outline/bordered pressables, segmented selected states, or ghost actions.",
  "One edge: the component paints ring or border; the shadow never paints a second ring.",
  "Never reach for stock Tailwind shadows (shadow-sm/md/lg); the four named rungs are the vocabulary.",
  "Depth responds to the press: what lifts on hover settles on active.",
  "The fill ladder stops at panel; above it, depth is shadow alone.",
] as const;

/*
 * Six is the ceiling because +4 (an overlay) on top of +2 (a popup that opened
 * one) is the deepest stack this product can produce. A menu inside a dialog is
 * already pinned at the top rung; going further would only spend shadow on
 * something nothing is above.
 */
const MAX_ELEVATION_LEVEL = 6;

/** Level 0 is the page. A surface that never wrapped anything reads as canvas. */
const SUBSTRATE_LEVEL = 0;

const SurfaceContext = React.createContext(SUBSTRATE_LEVEL);

const FILL_CLASS: readonly string[] = [
  "bg-canvas",
  "bg-panel",
  "bg-panel",
  "bg-panel",
  "bg-panel",
  "bg-panel",
  "bg-panel",
];

/*
 * The rung a level lands on. `raised-hover` is deliberately absent: it is the
 * one-step lift a pressable takes under the pointer, so it is reached by an
 * interaction variant on the control, never by sitting at a level.
 */
const SHADOW_CLASS: readonly string[] = [
  "",
  "shadow-control",
  "shadow-popup",
  "shadow-popup",
  "shadow-overlay",
  "shadow-overlay",
  "shadow-overlay",
];

/** The level a substrate plus an offset resolves to, clamped to the ladder. */
function elevationLevel(substrate: number, offset: number): number {
  return Math.min(Math.max(substrate + offset, SUBSTRATE_LEVEL), MAX_ELEVATION_LEVEL);
}

/** The fill and the rung a resolved level wears. */
function elevationClasses(level: number, shadowLevel: number): string {
  const fill = elevationLevel(level, 0);
  const rung = elevationLevel(shadowLevel, 0);
  return cn(FILL_CLASS[fill], SHADOW_CLASS[rung] || undefined);
}

/*
 * `data-*` keys are legal on a JSX tag but not in a typed props object, and
 * `useRender` takes an object. The cast is the whole reason this helper exists;
 * the attribute itself is how a resolved level stays inspectable in the DOM,
 * which is what makes a wrong nesting visible in devtools instead of arguable.
 */
function elevationAttributes(level: number): React.HTMLAttributes<HTMLDivElement> {
  return { "data-elevation": String(level) } as React.HTMLAttributes<HTMLDivElement>;
}

/** The level of the surface this subtree sits on. */
function useSurface(): number {
  return React.use(SurfaceContext);
}

/**
 * Re-provides the substrate level to a subtree. `Elevated` does this for you;
 * reach for the provider directly only when a surface is drawn by something
 * that is not an `Elevated` (a portalled popup's own container, a fixture that
 * wants to render a rung in isolation).
 */
function SurfaceProvider({
  value,
  children,
}: {
  value: number;
  children: React.ReactNode;
}) {
  return <SurfaceContext value={value}>{children}</SurfaceContext>;
}

type ElevatedProps = useRender.ComponentProps<"div"> & {
  /**
   * Steps above the substrate this landed on. +2 for an anchored popup, +4 for
   * a modal overlay; the resolved level is re-provided to descendants.
   */
  offset: number;
  /**
   * Pins the shadow rung while the fill keeps tracking the substrate. A popup
   * reads with the same weight whether it opened on the page or inside a
   * dialog, because a menu is a menu; only its ground changed.
   */
  shadowLevel?: number;
};

/** A surface that declares how far above its substrate it sits. */
function Elevated({
  offset,
  shadowLevel,
  className,
  render,
  ...props
}: ElevatedProps) {
  const substrate = useSurface();
  const level = elevationLevel(substrate, offset);

  const element = useRender({
    defaultTagName: "div",
    render,
    state: { slot: "elevated" },
    props: mergeProps<"div">(
      {
        className: cn(elevationClasses(level, shadowLevel ?? level), className),
        ...elevationAttributes(level),
      },
      props
    ),
  });

  return <SurfaceProvider value={level}>{element}</SurfaceProvider>;
}

export {
  Elevated,
  SurfaceProvider,
  useSurface,
  elevationLevel,
  elevationClasses,
  ELEVATION_RULES,
  MAX_ELEVATION_LEVEL,
};
