"use client";

/*
 * Slider, on CORE 14. The fluid-functionalism item (ANALYSIS.md row 40)
 * re-expressed on Base UI, because we shipped no slider at all and the
 * consumers are already named: churn-risk thresholds, LinkedIn daily send caps
 * and working-hour windows, drip delay days, chart date ranges.
 *
 * RADIUS, PER THE RATIO LAW. Three parts, three answers, and only one of them
 * is a ladder step. The track is a 4px bar, so `rounded-full` is its cap shape
 * exactly as it is Progress's -- half of 4px is 2px, and a bar with square ends
 * is a different object. The thumb is a 16px disc and is round for the reason a
 * radio is round: the circle IS its meaning, the shape that says "grab here and
 * push", and a 5px corner on a 16px square makes a tiny checkbox that happens to
 * slide. Both are the stated exception in wiki 02 ("radio, switch track, avatar,
 * progress cap, scrollbar thumb"), not a licence to round anything small. The
 * step dots are the same argument at 4px. Nothing here reaches for `panel` or
 * `control`: a slider has no containment, so it has no containment radius.
 *
 * DEPTH: THE THUMB PASSES THE PRESSABLE TEST, AND NOTHING ELSE HERE DOES. Wiki
 * 02: a shadow is an affordance (this can be pushed / this is in front of the
 * page) and belongs to pressables and overlays alone. The thumb is the most
 * literal pressable in the system -- it is picked up and moved -- so it takes
 * `shadow-control`, lifts to `shadow-raised-hover` under the pointer, and
 * settles flat while dragging, which is the same "settles flat on the press"
 * rule Button states: a control that keeps its lift while being pushed reads as
 * sliding away from the finger rather than under it. The track is containment
 * (a rail the value sits in) and grows no shadow, ever.
 *
 * MOTION IS THE STRICT TIER. The thumb's position is not animated at all: it is
 * `insetInlineStart` in percent, owned by Base UI, and a transition on it would
 * be animating a layout property AND putting the handle somewhere the finger is
 * not. What transitions is the box-shadow and the fill, at `duration-fast` on
 * `ease-out-quint`, and `motion-reduce` drops even that. No spring, no
 * proximity hover, no weight-on-interact: those are the three things the
 * reference item spends its size on and the three this file declines.
 *
 * THE READOUT RESERVES ITS LANE. A number that changes as you drag will resize
 * its own box unless something stops it, and a resizing readout drags the
 * label beside it. Two mechanisms, both from the registry read: `tabular-nums`
 * fixes the digit width, and the `inline-grid` ghost (technique 4) stacks an
 * invisible copy of the widest possible value in the same grid cell so the box
 * is measured once, by `formatValue(max)`, and never again.
 *
 * CLICK-TO-EDIT WRITES THROUGH THE THUMB'S OWN INPUT. Base UI's Thumb renders a
 * nested `<input type="range">` and treats a change on it as a first-class
 * value change (its documented `input-change` reason, the one form integration
 * uses). So the editable readout does not fabricate an event or fork the value:
 * it sets that input with the native value setter and dispatches `input`, and
 * the slider updates itself. That is why `editable` works on a controlled and
 * an uncontrolled slider alike, and why the caller's `onValueChange` fires with
 * real event details rather than something this file invented.
 */

import { Slider as SliderPrimitive } from "@base-ui/react/slider";
import { cva, type VariantProps } from "class-variance-authority";
import { useRef, useState, type KeyboardEvent } from "react";

import { cn } from "./cn";
import { Input } from "./input";
import { LABEL_CLASS } from "./label";

type SliderValue = number | readonly number[];

/** How many step dots may be drawn before the rail turns into a comb. */
const MAX_STEP_DOTS = 24;

const sliderControlVariants = cva(
  "relative flex w-full touch-none items-center select-none data-disabled:pointer-events-none data-disabled:opacity-50",
  {
    variants: {
      size: {
        compact: "h-control-sm",
        control: "h-control",
      },
    },
    defaultVariants: { size: "control" },
  }
);

/* The rail. Containment, so: a fill, a cap shape, and no shadow. */
const TRACK_CLASS = "h-1 w-full rounded-full bg-line-strong";

const INDICATOR_CLASS = "h-full rounded-full bg-primary";

/*
 * The one pressable. `has-[:focus-visible]` rather than `focus-visible`,
 * because the focusable element is the nested range input, not this box.
 */
const THUMB_CLASS =
  "size-4 rounded-full border border-line-strong bg-panel shadow-control transition-[box-shadow,background-color] duration-fast ease-out-quint outline-none has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-primary hover:shadow-raised-hover data-dragging:shadow-none data-disabled:shadow-none motion-reduce:transition-none";

/* A notch punched through the rail, readable over both the track and the fill. */
const STEP_DOT_CLASS =
  "pointer-events-none absolute top-1/2 size-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-canvas";

const HEADER_CLASS = "flex min-w-0 items-center justify-between gap-2";

const SLIDER_LABEL_CLASS = cn("min-w-0 truncate", LABEL_CLASS);

/* The ghost and the live value share one cell; see the header. */
const VALUE_GRID_CLASS = "inline-grid shrink-0 justify-items-end";
const VALUE_CELL_CLASS =
  "col-start-1 row-start-1 font-mono text-label text-ink-muted tabular-nums";
const VALUE_GHOST_CLASS = "invisible";

const VALUE_BUTTON_CLASS =
  "cursor-pointer rounded-badge px-1 text-right outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary";

const VALUE_INPUT_CLASS =
  "h-control-sm w-20 rounded-compact px-1.5 text-right font-mono text-label tabular-nums";

/** The default readout: the raw number, so a caller that formats opts in. */
function defaultFormatValue(value: number): string {
  return String(value);
}

/** One thumb, or one per entry when the slider is a range. */
function countThumbs(value: SliderValue | undefined): number {
  if (value === undefined || typeof value === "number") {
    return 1;
  }

  return Math.max(value.length, 1);
}

/**
 * Writes a value through the thumb's own range input, so Base UI reports it as
 * an `input-change` with real event details instead of us inventing one.
 */
function commitThroughInput(input: HTMLInputElement, value: number): void {
  const descriptor = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value"
  );

  descriptor?.set?.call(input, String(value));
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

/** Every step position as a percentage, or null when there are too many to draw. */
function stepPercentages(
  min: number,
  max: number,
  step: number
): readonly number[] | null {
  const span = max - min;

  if (span <= 0 || step <= 0) {
    return null;
  }

  const count = Math.round(span / step);

  if (count < 1 || count > MAX_STEP_DOTS) {
    return null;
  }

  return Array.from({ length: count + 1 }, (_, index) => (index / count) * 100);
}

/**
 * The number beside the label. Always in the mono face with `tabular-nums`, and
 * always measured by the ghost rather than by whatever it happens to say.
 */
function SliderReadout({
  editable,
  formatValue,
  max,
  min,
  onEdit,
  step,
}: {
  editable: boolean;
  formatValue: (value: number) => string;
  max: number;
  min: number;
  onEdit: (value: number) => void;
  step: number;
}) {
  const [editing, setEditing] = useState(false);
  const ghost = formatValue(max);

  function commit(raw: string) {
    setEditing(false);

    const parsed = Number.parseFloat(raw);

    if (Number.isNaN(parsed)) {
      return;
    }

    onEdit(Math.min(Math.max(parsed, min), max));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      commit(event.currentTarget.value);
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      setEditing(false);
    }
  }

  return (
    <SliderPrimitive.Value
      data-slot="slider-value"
      className={VALUE_GRID_CLASS}
    >
      {(formatted, values) => {
        const text = values.map((value) => formatValue(value)).join(" – ");
        const single = values.length === 1;
        const ghostText = single ? ghost : `${ghost} – ${ghost}`;

        return (
          <>
            <span
              aria-hidden="true"
              className={cn(VALUE_CELL_CLASS, VALUE_GHOST_CLASS)}
            >
              {ghostText}
            </span>
            {editing && single ? (
              <Input
                data-slot="slider-value-input"
                type="number"
                autoFocus
                defaultValue={values[0]}
                min={min}
                max={max}
                step={step}
                aria-label="Value"
                className={cn(VALUE_CELL_CLASS, VALUE_INPUT_CLASS)}
                onKeyDown={handleKeyDown}
                onBlur={(event) => {
                  commit(event.currentTarget.value);
                }}
              />
            ) : editable && single ? (
              <button
                type="button"
                data-slot="slider-value-trigger"
                className={cn(VALUE_CELL_CLASS, VALUE_BUTTON_CLASS)}
                onClick={() => {
                  setEditing(true);
                }}
              >
                {text || formatted.join(" – ")}
              </button>
            ) : (
              <span className={VALUE_CELL_CLASS}>
                {text || formatted.join(" – ")}
              </span>
            )}
          </>
        );
      }}
    </SliderPrimitive.Value>
  );
}

interface SliderProps
  extends Omit<SliderPrimitive.Root.Props<SliderValue>, "children" | "render">,
    VariantProps<typeof sliderControlVariants> {
  /** Names the slider above the rail. Omit it and pass `thumbLabels` instead. */
  label?: string;
  /** Shows the value opposite the label, in the mono face. */
  showValue?: boolean;
  /** Turns the readout into a number field on click. Single-value sliders only. */
  editable?: boolean;
  /** Draws a notch at every step, up to 24 of them. */
  showSteps?: boolean;
  /** Units, currency, days: whatever makes the number mean something. */
  formatValue?: (value: number) => string;
  /** One accessible name per thumb. A range needs two ("From", "To"). */
  thumbLabels?: readonly string[];
}

/**
 * One value, or a range, on a rail. Size is the density ladder; everything else
 * about the geometry is fixed, because a slider is a rail and a disc.
 */
function Slider({
  className,
  editable = false,
  formatValue = defaultFormatValue,
  label,
  max = 100,
  min = 0,
  showSteps = false,
  showValue = false,
  size = "control",
  step = 1,
  thumbLabels,
  ...props
}: SliderProps) {
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);
  const thumbs = countThumbs(props.value ?? props.defaultValue);
  const dots = showSteps ? stepPercentages(min, max, step) : null;

  return (
    <SliderPrimitive.Root
      data-slot="slider"
      min={min}
      max={max}
      step={step}
      className={cn("flex w-full flex-col gap-1.5", className)}
      {...props}
    >
      {label === undefined && !showValue ? null : (
        <div className={HEADER_CLASS}>
          {label === undefined ? null : (
            <SliderPrimitive.Label
              data-slot="slider-label"
              className={SLIDER_LABEL_CLASS}
            >
              {label}
            </SliderPrimitive.Label>
          )}
          {showValue ? (
            <SliderReadout
              editable={editable}
              formatValue={formatValue}
              max={max}
              min={min}
              step={step}
              onEdit={(value) => {
                const input = inputRefs.current[0];

                if (input !== null && input !== undefined) {
                  commitThroughInput(input, value);
                }
              }}
            />
          ) : null}
        </div>
      )}

      <SliderPrimitive.Control
        data-slot="slider-control"
        className={sliderControlVariants({ size })}
      >
        <SliderPrimitive.Track
          data-slot="slider-track"
          className={TRACK_CLASS}
        >
          <SliderPrimitive.Indicator
            data-slot="slider-indicator"
            className={INDICATOR_CLASS}
          />
          {dots?.map((percent) => (
            <span
              key={percent}
              aria-hidden="true"
              data-slot="slider-step-dot"
              style={{ insetInlineStart: `${percent}%` }}
              className={STEP_DOT_CLASS}
            />
          ))}
          {Array.from({ length: thumbs }, (_, index) => (
            <SliderPrimitive.Thumb
              key={index}
              index={index}
              data-slot="slider-thumb"
              className={THUMB_CLASS}
              aria-label={thumbLabels?.[index]}
              inputRef={(node) => {
                inputRefs.current[index] = node;
              }}
            />
          ))}
        </SliderPrimitive.Track>
      </SliderPrimitive.Control>
    </SliderPrimitive.Root>
  );
}

export { Slider, sliderControlVariants };
export type { SliderProps, SliderValue };
