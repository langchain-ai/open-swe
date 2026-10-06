/*
 * Design-system-aware class merge.
 *
 * tailwind-merge does not read our CSS, so it classifies `text-label` /
 * `text-meta` / `text-body` / `text-title` / `text-page` as text *colours*
 * (anything after `text-` that is not a known font-size scale value). Paired
 * with a real colour it then drops the size: `cn("text-label", "text-ink")`
 * returned only `text-ink`, and the whole type scale vanished from the DOM.
 *
 * Registering the five CORE 14 sizes in the font-size group restores the
 * intended behaviour: size and colour coexist, size still overrides size, and
 * colour still overrides colour. Vendored shadcn class names are unaffected.
 *
 * The geometry tokens have the same failure mode in the other direction:
 * unregistered values are unclassifiable, so `cn("h-control", "h-control-sm")`
 * kept both classes and the winner was decided by stylesheet order — heights
 * happened to win, radii happened to lose. Registering the spacing and radius
 * ladders makes call-site overrides of a primitive's geometry deterministic.
 *
 * This is deliberately a UI-layer helper. When the vendored components are
 * retokenized, promote it to `../lib/utils` and delete the plain `cn` there.
 */

import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

const TYPE_SCALE = ["meta", "label", "body", "title", "page", "display"];

const SPACING_SCALE = [
  "control-sm",
  "control",
  "row-data",
  "row-record",
  "row-convo",
  "toolbar",
  "badge",
  "composer",
];

const RADIUS_SCALE = ["tick", "badge", "compact", "control", "panel", "shell"];

const SHADOW_SCALE = ["control", "raised-hover", "popup", "overlay"];

const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: TYPE_SCALE }],
      h: [{ h: SPACING_SCALE }],
      "min-h": [{ "min-h": SPACING_SCALE }],
      "max-h": [{ "max-h": SPACING_SCALE }],
      w: [{ w: SPACING_SCALE }],
      "min-w": [{ "min-w": SPACING_SCALE }],
      "max-w": [{ "max-w": SPACING_SCALE }],
      size: [{ size: SPACING_SCALE }],
      rounded: [{ rounded: RADIUS_SCALE }],
      shadow: [{ shadow: SHADOW_SCALE }],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
