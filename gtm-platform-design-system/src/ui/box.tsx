"use client";

/*
 * Typed layout primitives (the Orbit escalation, wiki 02).
 *
 * Layout is a decision, not a value: `padding="md"` is expressible, `p-3` is not.
 * Every prop is a closed token union mapped through a module-scope lookup to a
 * literal utility string, so invalid geometry is a type error before lint runs
 * and Tailwind always sees whole class names.
 */

import { mergeProps } from "@base-ui/react/merge-props";
import { useRender } from "@base-ui/react/use-render";

import { cn } from "./cn";

type SpaceStep = "none" | "xs" | "sm" | "md" | "lg" | "xl" | "2xl";
type SurfaceFill =
  | "none"
  | "canvas"
  | "panel"
  | "sidebar"
  | "muted"
  | "selected"
  | "hover";
type RadiusStep = "none" | "badge" | "compact" | "control" | "panel" | "shell";
/*
 * The ink a surface can set on its subtree. Three neutral steps plus the five
 * state inks, and nothing else: the CORE 14 text vocabulary, as a type.
 *
 * Fills were already a union (`bg`) while colour for TEXT still arrived as a
 * class that only lint could judge, which made ink the weakest wall in the
 * system - wrong at the keystroke, right only once eslint ran. A union is
 * refused by the compiler instead.
 */
type InkTone =
  | "ink"
  | "ink-muted"
  | "ink-subtle"
  | "positive"
  | "attention"
  | "risk"
  | "info"
  | "neutral";
type BorderWeight = "none" | "line" | "line-strong";
type AlignItems = "start" | "center" | "end" | "stretch" | "baseline";
type JustifyContent =
  | "start"
  | "center"
  | "end"
  | "between"
  | "around"
  | "evenly";

const INK_CLASS: Record<InkTone, string> = {
  attention: "text-attention",
  info: "text-info",
  ink: "text-ink",
  "ink-muted": "text-ink-muted",
  "ink-subtle": "text-ink-subtle",
  neutral: "text-neutral",
  positive: "text-positive",
  risk: "text-risk",
};

const PADDING_CLASS: Record<SpaceStep, string> = {
  none: "p-0",
  xs: "p-1",
  sm: "p-2",
  md: "p-3",
  lg: "p-4",
  xl: "p-6",
  "2xl": "p-8",
};

const GAP_CLASS: Record<SpaceStep, string> = {
  none: "gap-0",
  xs: "gap-1",
  sm: "gap-2",
  md: "gap-3",
  lg: "gap-4",
  xl: "gap-6",
  "2xl": "gap-8",
};

const FILL_CLASS: Record<SurfaceFill, string> = {
  none: "bg-transparent",
  canvas: "bg-canvas",
  panel: "bg-panel",
  sidebar: "bg-sidebar",
  muted: "bg-muted",
  selected: "bg-selected",
  hover: "bg-hover",
};

const RADIUS_CLASS: Record<RadiusStep, string> = {
  none: "rounded-none",
  badge: "rounded-badge",
  compact: "rounded-compact",
  control: "rounded-control",
  panel: "rounded-panel",
  shell: "rounded-shell",
};

const BORDER_CLASS: Record<BorderWeight, string> = {
  none: "border-0",
  line: "border border-line",
  "line-strong": "border border-line-strong",
};

const ALIGN_CLASS: Record<AlignItems, string> = {
  start: "items-start",
  center: "items-center",
  end: "items-end",
  stretch: "items-stretch",
  baseline: "items-baseline",
};

const JUSTIFY_CLASS: Record<JustifyContent, string> = {
  start: "justify-start",
  center: "justify-center",
  end: "justify-end",
  between: "justify-between",
  around: "justify-around",
  evenly: "justify-evenly",
};

/* flex-1 rather than grow: equal-basis columns are what callers actually mean. */
const GROW_CLASS = "flex-1";
const WRAP_CLASS = "flex-wrap";

interface SurfaceProps {
  /** Inner spacing, 4/8/12/16/24/32. */
  padding?: SpaceStep;
  /** Surface fill; the only fills app code can express. */
  bg?: SurfaceFill;
  /** Text colour for the subtree; the only inks app code can express. */
  ink?: InkTone;
  /** Radius ladder step, 6/8/10/12/14. */
  radius?: RadiusStep;
  /** Hairline weight; both weights draw a 1px border. */
  border?: BorderWeight;
}

interface FlexProps extends SurfaceProps {
  gap?: SpaceStep;
  align?: AlignItems;
  justify?: JustifyContent;
  /** Take the remaining main-axis space (flex-1). */
  grow?: boolean;
}

/* An unset prop emits nothing; the "none" step exists to reset an inherited value. */
function surfaceClass({
  padding,
  bg,
  ink,
  radius,
  border,
}: SurfaceProps): string {
  return cn(
    padding === undefined ? undefined : PADDING_CLASS[padding],
    bg === undefined ? undefined : FILL_CLASS[bg],
    ink === undefined ? undefined : INK_CLASS[ink],
    radius === undefined ? undefined : RADIUS_CLASS[radius],
    border === undefined ? undefined : BORDER_CLASS[border]
  );
}

type BoxProps = useRender.ComponentProps<"div"> & SurfaceProps;

/** A block container. Use `render` for semantic elements (`<section>`, `<h2>`, `<li>`). */
function Box({
  padding,
  bg,
  ink,
  radius,
  border,
  className,
  render,
  ...props
}: BoxProps) {
  return useRender({
    defaultTagName: "div",
    render,
    state: { slot: "box" },
    props: mergeProps<"div">(
      {
        className: cn(surfaceClass({ padding, bg, ink, radius, border }), className),
      },
      props
    ),
  });
}

type StackProps = useRender.ComponentProps<"div"> & FlexProps;

/** A vertical flex container. */
function Stack({
  padding,
  bg,
  ink,
  radius,
  border,
  gap = "none",
  align = "stretch",
  justify = "start",
  grow,
  className,
  render,
  ...props
}: StackProps) {
  return useRender({
    defaultTagName: "div",
    render,
    state: { slot: "stack" },
    props: mergeProps<"div">(
      {
        className: cn(
          "flex flex-col",
          GAP_CLASS[gap],
          ALIGN_CLASS[align],
          JUSTIFY_CLASS[justify],
          grow === true ? GROW_CLASS : undefined,
          surfaceClass({ padding, bg, ink, radius, border }),
          className
        ),
      },
      props
    ),
  });
}

type InlineProps = useRender.ComponentProps<"div"> &
  FlexProps & {
    /** Allow items to wrap onto more than one line. */
    wrap?: boolean;
  };

/** A horizontal flex container. */
function Inline({
  padding,
  bg,
  ink,
  radius,
  border,
  gap = "none",
  align = "center",
  justify = "start",
  grow,
  wrap,
  className,
  render,
  ...props
}: InlineProps) {
  return useRender({
    defaultTagName: "div",
    render,
    state: { slot: "inline" },
    props: mergeProps<"div">(
      {
        className: cn(
          "flex flex-row",
          GAP_CLASS[gap],
          ALIGN_CLASS[align],
          JUSTIFY_CLASS[justify],
          grow === true ? GROW_CLASS : undefined,
          wrap === true ? WRAP_CLASS : undefined,
          surfaceClass({ padding, bg, ink, radius, border }),
          className
        ),
      },
      props
    ),
  });
}

export { Box, Inline, Stack };
export type {
  AlignItems,
  BorderWeight,
  BoxProps,
  InlineProps,
  JustifyContent,
  RadiusStep,
  SpaceStep,
  StackProps,
  SurfaceFill,
};
