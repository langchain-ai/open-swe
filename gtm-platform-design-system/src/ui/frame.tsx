"use client";

/*
 * Frame, retokenized onto CORE 14.
 *
 * Vendored from ReUI's free-tier `frame` registry item
 * (https://reui.io/r/frame.json, fetched keyless 2026-08-05, `free: true`).
 * No licence key was used or is required. Its row in web/reference/VENDORED.md
 * carries the status.
 *
 * WHAT IT IS. A frame is a mat with panels on it: one recessed container that
 * holds one or more committed objects. It is the only sanctioned way to show a
 * panel inside another surface, and it is why "never nest a card in a card"
 * stays true -- the outer thing is a mat, not a card, and it is drawn as one.
 *
 * NOT FOR SINGLE SURFACES. Chat bubbles, flat feeds, and speech blocks are ONE
 * pad on ONE fill — compose `Box` / `Stack`. Frame always paints mat gutter +
 * panel pad (two insets). Using it for AgentInterpretation / ChangeFeed is how
 * "double padding" keeps coming back. Artifacts with multiple panels
 * (ApprovalArtifact, Receipt, VersionedEditor) are the Frame consumers.
 *
 * THE CONCENTRIC RADIUS, KEPT. Upstream's one real idea is that the panel's
 * corner is *derived* from the frame's, not copied from it. A panel sits inset
 * from the frame's outer edge by the frame's border plus its gutter, so its
 * radius is the frame radius minus that same inset; subtracting keeps the two
 * arcs parallel instead of leaving a fat wedge in every corner. That is the
 * containment-depth law expressed as arithmetic, so it survives the port
 * unchanged:
 *
 *   bordered   --frame-panel-radius = --frame-radius - --frame-gutter - 1px
 *   ghost      --frame-panel-radius = --frame-radius - --frame-gutter
 *   dense      --frame-panel-radius = --frame-radius
 *
 * With the defaults that is 12 - 4 - 1 = 7px. Seven is not a step on the radius
 * ladder and does not need to be: the ladder governs radii a builder *chooses*,
 * and this one is a consequence of two ladder values and a hairline. Change
 * --frame-radius (the single knob, defaulting to rounded-panel) and every panel
 * corner follows.
 *
 * STACKED + DENSE OUTER EDGE. The frame owns the outline AND the corner clip
 * (`overflow-hidden` + frame radius). Panels drop border and radius entirely —
 * they are fill bands with only the inter-panel seam (`border-b` on all but
 * last). Squaring via `rounded-t-none` / `rounded-b-none` is not enough here:
 * the panel still carries `rounded-(--frame-panel-radius)` (a `border-radius`
 * shorthand), and a VersionedEditor / PublishGate read as two pills with
 * rounded tops at the join. Bleeding borders with `-mx-px` also painted two
 * hairlines on the chrome footer. One outline, square bands, seams inside.
 *
 * WHY CSS VARIABLES AND NOT DESCENDANT SELECTORS. The frame has to reach its
 * panels -- to set their fill and their radius -- without knowing them. Doing
 * that with `[&_[data-slot=frame-panel]]:bg-panel` would win on specificity
 * against a caller's `<FramePanel className="bg-hover">`, so the escape hatch
 * would stop working. Variables land on the panel as ordinary single-class
 * utilities instead, and a class passed at the call site merges over them
 * through `cn` like anywhere else in the system.
 *
 * WHAT WAS DROPPED IN THE PORT.
 *   - The `shadow-xs` and the `before:` inner-glow pseudo-element. Depth here is
 *     surface plus hairline; there is no shadow token and this is not the
 *     component that invents one.
 *   - Upstream's twelve header/footer padding variables and their `*-adjust`
 *     knobs. The panel owns its padding and its internal gap; the header and
 *     the footer are unpadded flex columns inside it. That removes the
 *     double-inset upstream ships (panel px plus header px on the same text)
 *     and leaves the spacing prop meaning exactly one thing.
 *   - The per-spacing `mt-*` margins on the non-stacked variant, which
 *     duplicated the frame's own gap.
 *
 * SPACING. The prop names a step on the padding ladder and drives the panel:
 * sm 8, default 12, lg 16. The panel's internal gap (header to body to footer)
 * takes the step below it: 4, 8, 12 -- chrome sits tighter than content. The
 * frame's own gutter is a fixed 4px mat and is deliberately not on the spacing
 * axis: it is the inset the concentric radius is derived from, and growing it
 * past the frame radius would drive the panel corner negative.
 *
 * MOTION. None. A frame is structure; nothing about it animates.
 */

import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "./cn";

const frameVariants = cva(
  [
    "relative flex flex-col bg-clip-padding",
    "[--frame-radius:var(--radius-panel)] [--frame-gutter:--spacing(1)] [--frame-gap:var(--frame-gutter)]",
    "[--frame-panel-bg:var(--color-panel)]",
    "gap-(--frame-gap) rounded-(--frame-radius) p-(--frame-gutter)",
  ],
  {
    variants: {
      variant: {
        /* The mat recedes; the panels sit on it. */
        default:
          "border border-line bg-muted [--frame-panel-radius:calc(var(--frame-radius)_-_var(--frame-gutter)_-_1px)]",
        /* Inverted depth: the mat is the panel surface and the panels recede. */
        inverse:
          "border border-line bg-panel [--frame-panel-bg:var(--color-muted)] [--frame-panel-radius:calc(var(--frame-radius)_-_var(--frame-gutter)_-_1px)]",
        /* No mat and no hairline, so the inset loses its border term. */
        ghost:
          "[--frame-panel-radius:calc(var(--frame-radius)_-_var(--frame-gutter))]",
      },
      spacing: {
        sm: "[--frame-panel-pad:--spacing(2)] [--frame-panel-gap:--spacing(1)]",
        default:
          "[--frame-panel-pad:--spacing(3)] [--frame-panel-gap:--spacing(2)]",
        lg: "[--frame-panel-pad:--spacing(4)] [--frame-panel-gap:--spacing(3)]",
      },
      stacked: {
        /*
         * Panels join into one bordered column: the gap closes, adjoining
         * corners square off, and the shared edge is drawn once.
         *
         * Target direct panel children (`>&`). The older `*:has-[+…]` /
         * `*:[[data-slot]+…]` forms never matched in the compiled CSS, so every
         * panel stayed fully rounded and a VersionedEditor read as two cards.
         */
        true: [
          "[--frame-gap:0px]",
          "[&>[data-slot=frame-panel]:not(:last-child)]:rounded-b-none",
          "[&>[data-slot=frame-panel]:not(:first-child)]:rounded-t-none",
          "[&>[data-slot=frame-panel]:not(:first-child)]:border-t-0",
        ],
        false: "",
      },
      dense: {
        /*
         * The gutter goes to zero. With no inset left there is nothing to
         * subtract, so the panel corner matches the frame corner. Outer-edge
         * ownership when also stacked is the compound variant below — not a
         * `-mx-px` bleed (that doubled the footer hairline).
         */
        true: [
          "[--frame-gutter:0px] [--frame-panel-radius:var(--frame-radius)]",
        ],
        false: "",
      },
    },
    compoundVariants: [
      {
        /*
         * One outline (the frame). Panels are square fill bands with a single
         * seam — never their own radius and never a second box border on the
         * chrome footer. Frame clips the outer corners.
         */
        stacked: true,
        dense: true,
        class: [
          "overflow-hidden",
          "[&>[data-slot=frame-panel]]:rounded-none",
          "[&>[data-slot=frame-panel]]:border-0",
          "[&>[data-slot=frame-panel]:not(:last-child)]:border-b",
          "[&>[data-slot=frame-panel]:not(:last-child)]:border-line",
        ],
      },
    ],
    defaultVariants: {
      variant: "default",
      spacing: "default",
      stacked: false,
      dense: false,
    },
  }
);

type FrameProps = React.ComponentProps<"div"> &
  VariantProps<typeof frameVariants>;

/** The mat. Holds one or more panels and owns the radius every panel derives from. */
function Frame({
  className,
  variant,
  spacing,
  stacked,
  dense,
  ...props
}: FrameProps) {
  return (
    <div
      data-slot="frame"
      data-spacing={spacing ?? "default"}
      className={cn(
        frameVariants({ variant, spacing, stacked, dense }),
        className
      )}
      {...props}
    />
  );
}

type FramePanelProps = React.ComponentProps<"div"> & {
  /** Size the panel to its content instead of letting it fill the frame. */
  fit?: boolean;
  /**
   * Header / footer / toolbar strip. Keeps the frame's horizontal pad and a
   * compact vertical pad (`py-2.5`) — never `py-0`. Call sites that zero the
   * pad are how stacked cards kiss their border.
   */
  chrome?: boolean;
};

/** A committed object inside the frame. Owns its padding and its internal rhythm. */
function FramePanel({ className, chrome, fit, ...props }: FramePanelProps) {
  return (
    <div
      data-slot="frame-panel"
      data-chrome={chrome ? "true" : undefined}
      className={cn(
        "relative flex flex-col overflow-hidden border border-line bg-clip-padding",
        "rounded-(--frame-panel-radius) bg-(--frame-panel-bg)",
        "gap-(--frame-panel-gap)",
        chrome
          ? "min-h-row-record justify-center px-(--frame-panel-pad) py-2.5"
          : "p-(--frame-panel-pad)",
        fit !== true && "grow",
        className
      )}
      {...props}
    />
  );
}

/** Title and description block at the top of a panel. */
function FrameHeader({ className, ...props }: React.ComponentProps<"header">) {
  return (
    <header
      data-slot="frame-panel-header"
      className={cn("flex flex-col gap-1", className)}
      {...props}
    />
  );
}

/**
 * The panel's name. Defaults to `text-title` so the title leads a page
 * section. A dialog title is `text-label`; do not promote a card to
 * `text-page`. Chrome strips that need label-scale override down.
 */
function FrameTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="frame-panel-title"
      className={cn("text-title font-medium text-ink", className)}
      {...props}
    />
  );
}

/** One line of supporting copy under the title. */
function FrameDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="frame-panel-description"
      className={cn("text-meta text-ink-subtle", className)}
      {...props}
    />
  );
}

/** Actions or metadata at the foot of a panel; actions align right from sm up. */
function FrameFooter({ className, ...props }: React.ComponentProps<"footer">) {
  return (
    <footer
      data-slot="frame-panel-footer"
      className={cn(
        "flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-end",
        className
      )}
      {...props}
    />
  );
}

export {
  Frame,
  FrameDescription,
  FrameFooter,
  FrameHeader,
  FramePanel,
  FrameTitle,
  frameVariants,
};
export type { FramePanelProps, FrameProps };
