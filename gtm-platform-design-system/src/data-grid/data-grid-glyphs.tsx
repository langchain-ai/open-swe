/*
 * The grid's glyph set.
 *
 * ReUI ships its registry items against an `IconPlaceholder` that resolves to
 * whichever icon library the consuming project uses. This module used to answer
 * that by inlining Lucide's paths by hand at a matching 1.5 stroke, because
 * `<Icon>` took a glyph *component* and the library import was banned outside
 * `components/ui`. Both halves of that argument are gone: the product is on
 * Heroicons solid, where there is no stroke to match, and the glyph barrel
 * exports every drawing the grid needs.
 *
 * So this is no longer a second icon gate. Each export is a named 14px slot
 * over `<Icon>`, kept only so the suite's call sites read as grid chrome
 * (`<PinOffGlyph />`) rather than as product iconography, and so that swapping
 * a chrome drawing stays a one-line change here.
 *
 * Reaching for a *product* icon from a grid file is still wrong: that is
 * `<Icon>`'s job, straight from `ui/glyphs`.
 */

import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ChevronsUpDown,
  Columns,
  Pin,
  PinFilled,
  PinOff,
  PlusCircle,
} from "../ui/glyphs";
import { Icon } from "../ui/icon";

type GlyphProps = {
  className?: string;
};

/* The grid's chrome is 14px throughout: one rung, never a prop. */
function ArrowUpGlyph({ className }: GlyphProps) {
  return <Icon icon={ArrowUp} size="sm" className={className} />;
}

function ArrowDownGlyph({ className }: GlyphProps) {
  return <Icon icon={ArrowDown} size="sm" className={className} />;
}

function ArrowLeftGlyph({ className }: GlyphProps) {
  return <Icon icon={ArrowLeft} size="sm" className={className} />;
}

function ArrowRightGlyph({ className }: GlyphProps) {
  return <Icon icon={ArrowRight} size="sm" className={className} />;
}

/* "Pin to the left edge": the double chevron. */
function ArrowLeftToLineGlyph({ className }: GlyphProps) {
  return <Icon icon={ChevronsLeft} size="sm" className={className} />;
}

/* "Pin to the right edge". */
function ArrowRightToLineGlyph({ className }: GlyphProps) {
  return <Icon icon={ChevronsRight} size="sm" className={className} />;
}

function ChevronsUpDownGlyph({ className }: GlyphProps) {
  return <Icon icon={ChevronsUpDown} size="sm" className={className} />;
}

function ChevronLeftGlyph({ className }: GlyphProps) {
  return <Icon icon={ChevronLeft} size="sm" className={className} />;
}

function ChevronRightGlyph({ className }: GlyphProps) {
  return <Icon icon={ChevronRight} size="sm" className={className} />;
}

function ChevronDownGlyph({ className }: GlyphProps) {
  return <Icon icon={ChevronDown} size="sm" className={className} />;
}

function CheckGlyph({ className }: GlyphProps) {
  return <Icon icon={Check} size="sm" className={className} />;
}

function CirclePlusGlyph({ className }: GlyphProps) {
  return <Icon icon={PlusCircle} size="sm" className={className} />;
}

function ColumnsGlyph({ className }: GlyphProps) {
  return <Icon icon={Columns} size="sm" className={className} />;
}

function PinGlyph({ className }: GlyphProps) {
  return <Icon icon={Pin} size="sm" className={className} />;
}

function PinFilledGlyph({ className }: GlyphProps) {
  return <Icon icon={PinFilled} size="sm" className={className} />;
}

function PinOffGlyph({ className }: GlyphProps) {
  return <Icon icon={PinOff} size="sm" className={className} />;
}

export {
  ArrowDownGlyph,
  ArrowLeftGlyph,
  ArrowLeftToLineGlyph,
  ArrowRightGlyph,
  ArrowRightToLineGlyph,
  ArrowUpGlyph,
  CheckGlyph,
  ChevronDownGlyph,
  ChevronLeftGlyph,
  ChevronRightGlyph,
  ChevronsUpDownGlyph,
  CirclePlusGlyph,
  ColumnsGlyph,
  PinFilledGlyph,
  PinGlyph,
  PinOffGlyph,
};
export type { GlyphProps };
