"use client";

/*
 * Collapsible, on CORE 14.
 *
 * Disclosures expand through grid tracks without measuring height in JavaScript.
 * Reduced motion remains instant; `smooth={false}` opts out of panel motion.
 *
 * CHEVRON. `CollapsibleChevron` is a slot, not a decoration: it reads the
 * trigger's own `data-panel-open` through the `group/collapsible` the trigger
 * carries, so a caller writes the glyph once inside the trigger and never wires
 * a state prop to it. One element in both states, so the browser has something
 * to rotate instead of two glyphs to swap.
 */

import { Collapsible as CollapsiblePrimitive } from "@base-ui/react/collapsible";

import { ChevronRight } from "./glyphs";
import { cn } from "./cn";
import { Icon } from "./icon";

/*
 * The trigger owns the group name the chevron reads, and nothing else. A
 * disclosure header is a sidebar row, a setting row or a bare label depending
 * on the surface, so the geometry belongs to the caller (usually through
 * `render={<Button variant="ghost" />}`), never to this file.
 */
const TRIGGER_CLASS =
  "group/collapsible inline-flex items-center gap-1.5 rounded-compact text-left outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:pointer-events-none disabled:opacity-50";

const PANEL_CLASS =
  "grid grid-rows-[1fr] data-closed:grid-rows-[0fr] data-starting-style:grid-rows-[0fr]";
const SMOOTH_PANEL_CLASS =
  "transition-[grid-template-rows] duration-fast ease-out-quint motion-reduce:transition-none";

/* The clipper. `min-h-0` is what lets a grid item shrink below its content. */
const PANEL_INNER_CLASS = "min-h-0 overflow-hidden";

const CHEVRON_CLASS =
  "text-ink-subtle transition-transform duration-fast ease-out-quint group-data-[panel-open]/collapsible:rotate-90 motion-reduce:transition-none";

function Collapsible({ ...props }: CollapsiblePrimitive.Root.Props) {
  return <CollapsiblePrimitive.Root data-slot="collapsible" {...props} />;
}

function CollapsibleTrigger({
  className,
  ...props
}: CollapsiblePrimitive.Trigger.Props) {
  return (
    <CollapsiblePrimitive.Trigger
      data-slot="collapsible-trigger"
      className={cn(TRIGGER_CLASS, className)}
      {...props}
    />
  );
}

/** The disclosure glyph. Renders inside a `CollapsibleTrigger`; takes no state. */
function CollapsibleChevron({ className }: { className?: string }) {
  return (
    <Icon
      icon={ChevronRight}
      size="sm"
      className={cn(CHEVRON_CLASS, className)}
    />
  );
}

function CollapsibleContent({
  className,
  children,
  smooth = true,
  ...props
}: CollapsiblePrimitive.Panel.Props & { smooth?: boolean }) {
  return (
    <CollapsiblePrimitive.Panel
      data-slot="collapsible-content"
      className={cn(PANEL_CLASS, smooth && SMOOTH_PANEL_CLASS, className)}
      {...props}
    >
      <div data-slot="collapsible-content-inner" className={PANEL_INNER_CLASS}>
        {children}
      </div>
    </CollapsiblePrimitive.Panel>
  );
}

export {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
};
