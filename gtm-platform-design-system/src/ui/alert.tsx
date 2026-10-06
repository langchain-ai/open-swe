/*
 * Alert, on CORE 14. API reference: the ReUI free `alert` (reui.io/r/alert.json,
 * fetched keyless 2026-08-05) -- its part names, its `data-slot` anatomy and its
 * icon-plus-content composition are kept; its class vocabulary is not, because
 * ReUI paints from `--destructive` / `--success` / `--warning` and we paint from
 * the five CORE 14 state pairs.
 *
 * QUIET BY LAW. An alert is a tinted background, the matching ink, and a
 * hairline. It is never a fill: the ink-filled treatment is Badge's `urgent`
 * tier, rationed to one per region, and a block of prose is the last place that
 * ration should be spent. Tone therefore says *what kind of message this is*,
 * never *how loudly to shout it*, and a surface that needs more volume than
 * `risk` needs a dialog, not a louder alert.
 *
 * Quiet risk on Badge is the same tinted fill this uses. A callout is a block
 * with one message in it, so `--gtm-risk-bg` is the wash; a table cell is a
 * Quiet chip of the same pair, never Urgent.
 *
 * CONTAINMENT. `rounded-panel` and a hairline: an alert is a committed,
 * self-contained object, so it takes the panel step. That also means it obeys
 * "never nest a panel in a panel" -- an alert belongs flat on canvas or at the
 * head of a section, not inside a card.
 *
 * ANNOUNCEMENT is the caller's, not the primitive's. Most of our callouts render
 * with the page (a policy note, a state explanation) and a live region there
 * only makes a screen reader read the page twice; the same component arriving
 * after a failed send genuinely is an alert. So this file imposes no role and no
 * `aria-live`, and a surface that renders one in response to an event passes
 * `role="alert"` through. Guessing wrong in the primitive is worse than asking.
 *
 * NO ACTION SLOT, on purpose. ReUI ships `AlertAction`; the boards say what
 * belongs there is a *recovery* -- the tool-error row carries both a
 * preserved-work reassurance and a "Reconnect ->" link (Page-1 board 48) -- and
 * "what a recovery affordance looks like" is a patterns-tier decision, not a
 * primitive's. It lands with that pattern or not at all.
 */

import type { Glyph } from "./glyphs";

import { cn } from "./cn";
import { Icon } from "./icon";

type AlertTone = "info" | "positive" | "attention" | "risk" | "neutral";

/*
 * The pair, plus the hairline at the same 20% the quiet badge tier uses, so a
 * callout and a chip of the same tone share one edge weight.
 */
const ALERT_TONE_CLASS: Record<AlertTone, string> = {
  info: "border-info/20 bg-info-bg text-info",
  positive: "border-positive/20 bg-positive-bg text-positive",
  attention: "border-attention/20 bg-attention-bg text-attention",
  risk: "border-risk/20 bg-risk-bg text-risk",
  neutral: "border-neutral/20 bg-neutral-bg text-neutral",
};

interface AlertProps extends React.ComponentProps<"div"> {
  /** Which of the five state pairs this message belongs to. */
  tone?: AlertTone;
  /** Optional leading glyph; rendered through `<Icon>` at the 16px slot. */
  icon?: Glyph;
}

function Alert({
  className,
  tone = "info",
  icon,
  children,
  ...props
}: AlertProps) {
  return (
    <div
      data-slot="alert"
      data-tone={tone}
      className={cn(
        "flex w-full items-start gap-2.5 rounded-panel border px-3 py-2.5",
        ALERT_TONE_CLASS[tone],
        className
      )}
      {...props}
    >
      {/* The 16px glyph needs no nudge: text-label is 13 on a 16px line box. */}
      {icon === undefined ? null : <Icon icon={icon} size="md" />}
      <div data-slot="alert-content" className="flex min-w-0 flex-col gap-1">
        {children}
      </div>
    </div>
  );
}

/*
 * Title and description separate by weight, not by a second colour: a state
 * pair has one ink, and reaching for `text-ink-subtle` here would put a grey
 * paragraph on a tinted ground that was contrast-checked against its own ink.
 */
function AlertTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-title"
      className={cn("text-label font-medium", className)}
      {...props}
    />
  );
}

function AlertDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-description"
      className={cn("text-label", className)}
      {...props}
    />
  );
}

export { Alert, AlertDescription, AlertTitle };
export type { AlertProps, AlertTone };
