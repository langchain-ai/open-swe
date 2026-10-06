"use client";

/*
 * Badge, retokenized onto CORE 14.
 *
 * Status tiers (quiet / notable / plain / urgent) share fixed geometry: 20px
 * tall, 6px horizontal padding, badge radius, and a 1px border (transparent
 * when unused) so a filled badge and an outlined badge measure identically.
 * Quiet is the tinted fill: every tone, including risk, spends its `-bg`
 * token. Notable is outline. Plain is ink only. The `chip` tier is the
 * **compact number**: bare mono tabular meta beside a tab / filter label —
 * no fill, no pill geometry — same reading as sidebar-nav counts. Urgent is
 * the solid high-contrast ink pill (secondary fill); it is rationed to one
 * per region by review, not by the type.
 *
 * THE STATE DOT (adopted 2026-08-05 from fluid-functionalism's `badge`, whose
 * two variants are `solid` and `dot`). Fluid makes the dot a variant, which
 * would collapse into the tier axis here and the tier axis is closed on
 * surface treatment. It is an orthogonal boolean instead: `dot` prepends a
 * 6px disc to a badge of any tier and any tone, so a pill can say *which*
 * state it is (tone) and *that the state is live* (dot) without the two
 * answers competing for the same prop. Pair `dot` with `tier="plain"` for
 * label + color disc with no fill and no outline. The disc takes
 * `bg-current`, so it inherits whatever colour the tier x tone pair already
 * resolved -- one more token would have been one more way to disagree with
 * the label beside it. It is `aria-hidden`: the dot restates the label, and
 * a screen reader reading "green circle Connected" is noise.
 *
 * REJECTED from the same source, and why:
 *   - `size: sm | md | lg` (20/24/28px). Our badge is 20px, full stop -- a
 *     badge that can be 28 tall is a control wearing a pill, and the density
 *     ladder already owns 28.
 *   - The 17-colour Tailwind palette and its `color-mix(... 15%, background)`
 *     fill recipe. Tokens are the only colours (wiki 02 principle 3); the five
 *     tone pairs already ship the tinted fill that recipe computes.
 *   - The elevated/aggressive shadow ramp. Our reading of the elevation
 *     contract is scoped: shadows are a CONTROL-AFFORDANCE and OVERLAY
 *     treatment (pressables, active segments, popups, dialogs), and a badge is
 *     neither -- it is a state, not a thing you can press. A pill that looks
 *     liftable is a pill people will click. Hairline + tint stays.
 *   - `[text-box: trim-both cap alphabetic]` optical centring. It is the right
 *     idea for a fixed-height chip, but it is an arbitrary CSS property in app
 *     code and `text-box-trim` is not in this Tailwind's utility surface yet.
 *     It belongs in `@theme` with its first two consumers, not here.
 */

import { mergeProps } from "@base-ui/react/merge-props";
import { useRender } from "@base-ui/react/use-render";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "./cn";

const badgeVariants = cva(
  /*
   * MOTION. Tone is the only thing that moves, on the one sanctioned duration. A badge that stays
   * put while its subject changes state (a send settling from checking to delivered) reads as a new
   * badge rather than the same one answering differently. Nothing here moves or resizes.
   */
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 border text-meta font-medium whitespace-nowrap transition-[color,background-color,border-color] duration-fast ease-out-quint motion-reduce:transition-none [&>svg]:size-3 [&>svg]:shrink-0",
  {
    variants: {
      tier: {
        quiet: "h-badge rounded-badge px-1.5",
        notable: "h-badge rounded-badge px-1.5 bg-transparent",
        plain: "h-badge rounded-badge px-1.5 border-transparent bg-transparent",
        urgent:
          "h-badge rounded-badge px-1.5 border-transparent bg-secondary text-secondary-ink",
        /*
         * Compact number: bare meta numeral beside a tab label. Matches the
         * sidebar-nav count recipe (subtle + tabular) with the data face
         * (mono) — never a filled 16px pill that competes with the trigger.
         */
        chip:
          "h-auto min-w-0 rounded-none border-transparent bg-transparent px-0 font-mono font-normal tabular-nums text-ink-subtle",
      },
      tone: {
        positive: "",
        attention: "",
        risk: "",
        info: "",
        neutral: "",
      },
    },
    compoundVariants: [
      {
        tier: "quiet",
        tone: "positive",
        class: "border-positive/20 bg-positive-bg text-positive",
      },
      {
        tier: "quiet",
        tone: "attention",
        class: "border-attention/20 bg-attention-bg text-attention",
      },
      {
        tier: "quiet",
        tone: "risk",
        class: "border-risk/20 bg-risk-bg text-risk",
      },
      {
        tier: "quiet",
        tone: "info",
        class: "border-info/20 bg-info-bg text-info",
      },
      {
        tier: "quiet",
        tone: "neutral",
        class: "border-neutral/20 bg-neutral-bg text-neutral",
      },
      { tier: "notable", tone: "positive", class: "border-positive text-positive" },
      {
        tier: "notable",
        tone: "attention",
        class: "border-attention text-attention",
      },
      { tier: "notable", tone: "risk", class: "border-risk text-risk" },
      { tier: "notable", tone: "info", class: "border-info text-info" },
      { tier: "notable", tone: "neutral", class: "border-neutral text-neutral" },
      { tier: "plain", tone: "positive", class: "text-positive" },
      { tier: "plain", tone: "attention", class: "text-attention" },
      { tier: "plain", tone: "risk", class: "text-risk" },
      { tier: "plain", tone: "info", class: "text-info" },
      { tier: "plain", tone: "neutral", class: "text-neutral" },
    ],
    defaultVariants: {
      tier: "quiet",
      tone: "neutral",
    },
  }
);

/** The 6px state disc. `bg-current` so it can never disagree with its label. */
const DOT_CLASS = "size-1.5 shrink-0 rounded-full bg-current";

function Badge({
  className,
  tier = "quiet",
  tone = "neutral",
  dot = false,
  children,
  render,
  ...props
}: useRender.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { dot?: boolean }) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps<"span">(
      {
        className: cn(badgeVariants({ tier, tone }), className),
        children: dot ? (
          <>
            <span aria-hidden data-slot="badge-dot" className={DOT_CLASS} />
            {children}
          </>
        ) : (
          children
        ),
      },
      props
    ),
    render,
    state: {
      slot: "badge",
      tier,
      tone,
      dot,
    },
  });
}

export { Badge, badgeVariants };
