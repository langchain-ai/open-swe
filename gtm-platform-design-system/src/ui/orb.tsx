"use client";

/*
 * The orb: the agent's loading state, on CORE 14.
 *
 * This wraps `thinking-orbs` (MIT, zero runtime deps, plain 2D canvas) the same
 * way `ui/glyphs.ts` wraps Heroicons: the library is named in exactly one
 * module, and every call site reaches it through a closed vocabulary. The
 * containment argument is identical, and so is the failure it prevents -- an
 * import from the package in a feature file is a second, un-governed set of
 * loading states one refactor away from drifting off this one.
 *
 * WHAT IT IS. Dotted thought-orbs painted on a transparent canvas: no WebGL, no
 * CSS filters, no SVG filters, so the pixels are the same in every engine. Two
 * sizes ship, and they are separate hand-tuned designs rather than one drawing
 * scaled twice -- the same reason `<Icon>` spends three drawn Heroicons sets
 * instead of one. Naming them `inline` and `avatar` is deliberate: a rung is a
 * job, and `size={20}` at a call site is a measurement nobody can review.
 *
 * ACCESSIBILITY: THE CANVAS IS ALWAYS HIDDEN. The library ships the canvas as
 * `role="img"` with a per-state `aria-label` ("Searching...", "Composing..."),
 * which is wrong for us in every shipped call site, because all three already
 * own the announcement:
 *
 *   - `chat/tool-call-card.tsx` makes the step's own visible title the live
 *     region, and its rules forbid a second announcer in as many words: two
 *     live regions naming one step is the double-announcement failure that rule
 *     exists to prevent.
 *   - `chat/chat-interface.tsx` wraps the thread indicator in a labelled
 *     `role="status"`.
 *   - `chat/chat-thread-skeleton.tsx` is itself a `role="status"`.
 *
 * A named image beside a live region reading the same word is a stutter
 * ("Searching... Searching..."), and a live region beside a live region is a
 * double announcement. So the canvas is `aria-hidden` unconditionally and the
 * orb defaults to contributing nothing to the accessibility tree.
 *
 * The chosen escape is the second option, not `role="status"` on the canvas:
 * when the orb is genuinely the only sign that work is running, the call site
 * passes `label` and gets a real `sr-only` TEXT node inside a `role="status"`
 * wrapper. Text, not `aria-label`, because a live region announces the text
 * that appears inside it -- changing an `aria-label` on a region is not
 * reliably announced by assistive technology, so labelling the canvas would
 * have produced a status indicator that announces once and then goes silent
 * for the rest of the run. This is the same contract `ui/spinner.tsx` already
 * carries ("set only when the spinner is the only sign work is running"), which
 * is the point: one answer to one question, in both loading primitives.
 *
 * MOTION: A SANCTIONED EXCEPTION, WITH A BOUNDARY. The motion law is one
 * duration (`duration-fast`, 160ms) on one curve, and a continuously animating
 * canvas is neither. It is the same exception `ui/spinner.tsx` already holds
 * and states: motion that CARRIES STATE survives the law, motion that carries
 * delight does not. The boundary, which is the part that matters:
 *
 *   - an orb is on screen only while work is genuinely in flight, and it is
 *     unmounted the moment the work settles. It never animates to decorate a
 *     settled surface, never marks an empty state, and never stands in for a
 *     brand mark;
 *   - it is never the entrance or exit of anything. Enter/exit motion is still
 *     `duration-fast ease-out-quint` through tw-animate-css, on the container;
 *   - reduced motion collapses it to a single static frame -- and here it
 *     DIVERGES from Spinner, deliberately. A spinner keeps spinning under
 *     `prefers-reduced-motion` because stopping a 16px rotation turns "still
 *     working" into "frozen". An orb is forty-odd dots on tilted orbits across
 *     up to 64px, which is exactly the field-of-view motion the media query
 *     exists to remove; the still frame is legible as an orb on its own, and
 *     the "still working" message is carried by the label or the live region
 *     beside it in every call site. The library does this itself (verified in
 *     its source: under `prefers-reduced-motion: reduce` it paints one
 *     deterministic frame and never starts a rAF loop) and it still follows the
 *     live theme, so the wrapper adds nothing here beyond stating the rule.
 *
 * THEME. `theme="auto"` is the library's own resolution and it is our exact
 * convention: it walks ancestors for `data-theme="dark|light"` and watches
 * `documentElement` with a `MutationObserver`, which is precisely what
 * next-themes writes in `app/layout.tsx` (`attribute="data-theme"`). The palette
 * is strictly monochrome ink, so there is no colour decision to make and no
 * token to spend -- the orb reads as ink on canvas in both themes by
 * construction, and a component still never branches on theme.
 *
 * NO RADIUS, NO SHADOW. The canvas is transparent and unclipped; the orb is
 * drawn, not contained. Wrapping it in a filled, rounded, or elevated box would
 * spend the containment signal on a transient indicator and grow a shadow on a
 * non-pressable, both of which the elevation law forbids.
 */

import { ThinkingOrb, type OrbState } from "thinking-orbs";

import { Inline } from "./box";
import { cn } from "./cn";
import { StatusLabel } from "./spinner";

/**
 * What the agent is doing, in the product's own words.
 *
 * This is the vocabulary a call site is allowed to speak. It is our verb set,
 * derived from the tool families in `server/presentation/tool_activity.py`, and
 * it deliberately does NOT expose the library's nine animation names: an
 * animation is a look, and a look is not a thing a feature file gets to pick.
 */
type OrbActivity =
  | "working"
  | "thinking"
  | "searching"
  | "reading"
  | "analyzing"
  | "connecting"
  | "enriching"
  | "drafting"
  | "planning";

/**
 * THE MAP. A call site names what the agent is doing; this decides what that
 * looks like. It is the whole reason the primitive exists in this shape, and it
 * is written once so that no surface ever picks an orb by taste and no two
 * surfaces ever draw the same work differently.
 *
 * The reasoning per row, because a mapping without one is just a preference:
 */
const ORB_STATE_FOR_ACTIVITY: Record<OrbActivity, OrbState> = {
  /*
   * The honest default, and what every unmapped tool resolves to. Particles on
   * tilted orbits say "busy" and claim nothing more, which is exactly right for
   * work we cannot describe: the presentation contract's own fallback title is
   * "Reviewing requested information" for the same reason.
   */
  working: "working",
  /*
   * The model is generating and no tool is in flight. `breathing` is a face-on
   * ring slowly morphing -- alive rather than busy -- and the library labels it
   * "Thinking..." itself, which is a strong signal about what it was drawn for.
   */
  thinking: "breathing",
  /*
   * Going out for something we do not have yet: web and targeted web search,
   * company signals, LinkedIn search, internal Slack channels, customer
   * stories, glob and grep. A scan meridian sweeping a dotted globe is
   * literally the act of searching a body of material.
   */
  searching: "searching",
  /*
   * Taking in something already in hand: `read_file`, `ls`, account memory
   * files, an opened channel. Split from `searching` on the direction of
   * travel -- a query goes out, a read comes in -- and a waveform rolling
   * through the latitude rings is intake, not pursuit.
   */
  reading: "listening",
  /*
   * Reducing structured data to an answer: BigQuery, consumption. Bands
   * scramble in quarter turns and then click back solved, which is the shape of
   * a computation that resolves; nothing else in the set has a resolution.
   */
  analyzing: "solving",
  /*
   * Talking to a system of record or a third-party integration: Salesforce
   * reads and writes, Calendar, Gmail, Slack delivery, LinkedIn resolution,
   * Drive filing. A constellation wiring itself with packets running the edges
   * IS an integration call, and it is the one state in the set that depicts two
   * systems rather than one process.
   */
  connecting: "connecting",
  /*
   * Merging an external source into our own records: the Apollo family. Three
   * strands plaiting around the sphere is two sources becoming one record,
   * which is what enrichment is and what distinguishes it from a plain lookup.
   */
  enriching: "weaving",
  /*
   * Producing prose a human will read and send: email drafts, the bulk
   * composer, file writes, working notes. An undulating multi-band sash is the
   * closest thing in the set to a written line, and drafting is the one
   * activity whose output is language.
   */
  drafting: "composing",
  /*
   * Giving a run its shape: todo writes, delegation setup. A dotted outline
   * morphing circle to triangle to square is a plan taking form, and it is the
   * only state that depicts a thing acquiring structure rather than a thing
   * being processed.
   */
  planning: "shaping",
};

/**
 * The two rungs, named for their job. `inline` sits on a line of text beside a
 * label; `avatar` is the thread-scale indicator that stands alone. There is no
 * third rung and no free number, because the library ships exactly two tuned
 * designs and a scaled orb is a different, worse drawing.
 */
type OrbRung = "inline" | "avatar";

const ORB_RUNG_PX: Record<OrbRung, 20 | 64> = {
  inline: 20,
  avatar: 64,
};

/**
 * Visible verb for `OrbStatus`. These are the product words, not the library
 * animation names — the same closed set `activity` already is. The ellipsis is
 * the site's own caption (`orbs.jakubantalik.com`); the orb is the motion, so
 * the word does not pulse.
 */
const ORB_CAPTION: Record<OrbActivity, string> = {
  analyzing: "Analyzing…",
  connecting: "Connecting…",
  drafting: "Drafting…",
  enriching: "Enriching…",
  planning: "Planning…",
  reading: "Reading…",
  searching: "Searching…",
  thinking: "Thinking…",
  working: "Working…",
};

/** The rules this file holds, for the agent that edits the next call site. */
const ORB_RULES: readonly string[] = [
  "A call site names what the agent is DOING, never what the orb looks like. `activity` is the product's verb set; the library's nine animation names are not exported and must not be. A recurring product question gets one canonical answer, and 'which orb for this tool' is that question -- ORB_STATE_FOR_ACTIVITY is the answer, with its reasoning attached.",
  "Never import `thinking-orbs` outside this file. It is the same closed-set argument as `ui/glyphs.ts` for Heroicons: one module names the library, everything else reaches it through the wrapper, and the vocabulary stays reviewable.",
  "There are two sizes because the library ships two hand-tuned designs, not a scale factor. `inline` sits beside text, `avatar` stands alone at thread scale. Never scale an orb with a transform or a width -- that is a different, blurrier drawing.",
  "An orb is on screen only while work is genuinely in flight, and unmounts the moment it settles. It is never an empty state, never a brand mark, never decoration on a settled surface. This is the boundary that makes the continuous animation a sanctioned exception to the one-duration motion law rather than a hole in it.",
  "The canvas is `aria-hidden`, always. Every shipped call site already owns a live region, and a second announcer naming the same step is the double-announcement failure `chat/tool-call-card.tsx` exists to prevent. When the orb IS the only sign work is running, pass `label` and get a real `sr-only` text node in a `role=status` wrapper -- text, because a live region announces text that appears in it and does not reliably announce an `aria-label` that changed.",
  "Reduced motion collapses the orb to a single static frame, and here it diverges from `ui/spinner.tsx` on purpose: a 16px rotation is not a vestibular trigger and stopping it would read as frozen, but forty dots orbiting across 64px is exactly the motion the media query removes. The still frame plus the label beside it still says 'working'.",
  "No radius, no fill, no shadow, no ring around an orb. It is drawn on a transparent canvas; containing it would spend the containment signal on a transient indicator and grow a shadow on something that is not pressable.",
  "The quiet thread mark is `OrbStatus`: the orb plus a StatusLabel of the activity verb (`Thinking…`), the pair the library's own site draws. The orb is the busy signal — the word does not pulse. A step that already has a title (tool-call-card, subagent) keeps Orb alone.",
];

interface OrbProps {
  /** What the agent is doing. The map, not the call site, picks the animation. */
  activity?: OrbActivity;
  /** `inline` beside text (20px), `avatar` standing alone (64px). */
  size?: OrbRung;
  /**
   * Set ONLY when the orb is the only sign that work is running. It becomes an
   * `sr-only` text node inside a `role="status"` wrapper; leave it unset beside
   * any existing live region or visible status text.
   */
  label?: string;
  /** Placement only. The orb owns its own geometry and has no colour to set. */
  className?: string;
}

/**
 * The agent's loading state: a dotted thought-orb whose animation is chosen by
 * `ORB_STATE_FOR_ACTIVITY` from the activity the call site names.
 */
function Orb({
  activity = "working",
  size = "inline",
  label,
  className,
}: OrbProps) {
  return (
    <span
      data-slot="orb"
      data-activity={activity}
      role={label ? "status" : undefined}
      className={cn("inline-flex shrink-0 items-center justify-center", className)}
    >
      <ThinkingOrb
        state={ORB_STATE_FOR_ACTIVITY[activity]}
        size={ORB_RUNG_PX[size]}
        theme="auto"
        aria-hidden
      />
      {label ? <span className="sr-only">{label}</span> : null}
    </span>
  );
}

interface OrbStatusProps {
  /** What the agent is doing. Also picks the default caption. */
  activity?: OrbActivity;
  /** `inline` beside the verb (thread), `avatar` for a standalone mark. */
  size?: OrbRung;
  /**
   * Override the activity verb. Use when the pair is naming a different job
   * (`Loading conversation`) rather than the activity itself.
   */
  caption?: string;
  className?: string;
  "data-testid"?: string;
}

/**
 * The orbs-site pair: a thinking-orb and the word that names the work.
 * Use this when the orb is the only visible sign that a turn is in flight.
 * Do not use it beside a step title that already names the work.
 */
function OrbStatus({
  activity = "thinking",
  caption,
  className,
  size = "inline",
  ...props
}: OrbStatusProps) {
  const word = caption ?? ORB_CAPTION[activity];
  return (
    <Inline
      data-slot="orb-status"
      data-activity={activity}
      role="status"
      gap="sm"
      align="center"
      className={className}
      {...props}
    >
      <Orb activity={activity} size={size} />
      <StatusLabel label={word} className="text-label text-ink-subtle" />
    </Inline>
  );
}

export { Orb, OrbStatus, ORB_CAPTION, ORB_RULES, ORB_RUNG_PX, ORB_STATE_FOR_ACTIVITY };
export type { OrbActivity, OrbProps, OrbRung, OrbStatusProps };
