"use client";

/*
 * MentionChip — Slack-shaped entity pill.
 *
 * ANATOMY. Badge geometry (20px / rounded-badge) + mark + label (+ optional ×).
 * A chip is a state atom, not a pressable, so it takes hairline + tint (or an
 * intentional solid / plain / outline face) and never a shadow.
 *
 * VARIANTS (closed set — product taste, named to sit beside Badge tiers
 * without cloning the Badge API onto chips):
 *   quiet   — tinted kind pill (Badge quiet × tone + icon). Product default.
 *   solid   — secondary fill (Badge urgent); kind only on the icon color.
 *   plain   — chrome-free (Badge plain × tone + dot); kind by color disc.
 *   notable — kind outline (Badge notable × tone + icon).
 *
 * PEEK. HoverCard (not Tooltip): a small document — kind, label, description.
 * Same 600/300 delay family as every other hover card so sweeping a list does
 * not open peeks. Peek always shows the kind icon, even when the face uses a
 * disc (plain).
 *
 * The primitive is presentational on purpose: it does not know about chat
 * catalogs. Callers (the composer, a transcript, the gallery) pass icon / tone
 * / copy. That keeps `ui/` free of product entity types.
 */

import type { VariantProps } from "class-variance-authority";

import { badgeVariants, Badge } from "./badge";
import { cn } from "./cn";
import type { Glyph } from "./glyphs";
import { X } from "./glyphs";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "./hover-card";
import { Icon } from "./icon";

type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

/** Closed set of opinionated faces. Prefer quiet in product surfaces. */
type MentionChipVariant = "quiet" | "solid" | "plain" | "notable";

interface MentionChipProps {
  /** Primary label on the pill. */
  label: string;
  /** Kind heading shown in the peek (Accounts, Campaigns, …). */
  kindLabel: string;
  icon: Glyph;
  tone?: BadgeTone;
  /**
   * Visual face. Default `quiet` (tinted kind pill) is the product choice;
   * gallery explores solid / plain / notable for comparison.
   */
  variant?: MentionChipVariant;
  description?: string;
  /** Stable id for tests / keys. */
  id?: string;
  /** Show the hover peek. Default true. */
  peek?: boolean;
  /** When set, an × appears to dismiss the chip. */
  onRemove?: () => void;
  className?: string;
}

const TONE_INK: Record<BadgeTone, string> = {
  info: "text-info",
  attention: "text-attention",
  positive: "text-positive",
  neutral: "text-neutral",
  risk: "text-risk",
};

function MentionChipFace({
  label,
  icon,
  tone = "neutral",
  variant = "quiet",
  id,
  onRemove,
  className,
}: Pick<
  MentionChipProps,
  "label" | "icon" | "tone" | "variant" | "id" | "onRemove" | "className"
>) {
  const removeControl = onRemove ? (
    <button
      type="button"
      aria-label={`Remove ${label}`}
      data-testid={id ? `mention-chip-remove-${id}` : "mention-chip-remove"}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onRemove();
      }}
      className="-mr-0.5 flex size-3 shrink-0 cursor-pointer items-center justify-center rounded-full opacity-70 transition-opacity duration-fast ease-out-quint hover:opacity-100"
    >
      <Icon icon={X} size="sm" />
    </button>
  ) : null;

  const testId = id ? `mention-chip-${id}` : "mention-chip";
  const faceClass = cn("max-w-56 align-middle", className);
  const labelSpan = <span className="min-w-0 truncate">{label}</span>;

  switch (variant) {
    case "solid":
      return (
        <Badge
          tier="urgent"
          data-testid={testId}
          data-variant={variant}
          className={faceClass}
        >
          <Icon icon={icon} className={TONE_INK[tone]} />
          {labelSpan}
          {removeControl}
        </Badge>
      );
    case "plain":
      return (
        <Badge
          tier="plain"
          tone={tone}
          dot
          data-testid={testId}
          data-variant={variant}
          className={faceClass}
        >
          {labelSpan}
          {removeControl}
        </Badge>
      );
    case "notable":
      return (
        <Badge
          tier="notable"
          tone={tone}
          data-testid={testId}
          data-variant={variant}
          className={faceClass}
        >
          <Icon icon={icon} />
          {labelSpan}
          {removeControl}
        </Badge>
      );
    case "quiet":
      return (
        <Badge
          tier="quiet"
          tone={tone}
          data-testid={testId}
          data-variant={variant}
          className={faceClass}
        >
          <Icon icon={icon} />
          {labelSpan}
          {removeControl}
        </Badge>
      );
    default: {
      const _exhaustive: never = variant;
      throw new Error(`Unhandled MentionChip variant: ${String(_exhaustive)}`);
    }
  }
}

function MentionChipPeek({
  label,
  kindLabel,
  icon,
  tone = "neutral",
  description,
}: Pick<
  MentionChipProps,
  "label" | "kindLabel" | "icon" | "tone" | "description"
>) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2.5">
        <span
          className={cn(
            "flex size-control shrink-0 items-center justify-center rounded-compact border",
            tone === "info" && "border-info/20 bg-info-bg text-info",
            tone === "attention" &&
              "border-attention/20 bg-attention-bg text-attention",
            tone === "positive" &&
              "border-positive/20 bg-positive-bg text-positive",
            tone === "neutral" && "border-neutral/20 bg-neutral-bg text-neutral",
            tone === "risk" && "border-risk bg-transparent text-risk"
          )}
        >
          <Icon icon={icon} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-label font-medium text-ink">{label}</p>
          <p className="truncate text-meta text-ink-subtle">{kindLabel}</p>
        </div>
      </div>
      {description && (
        <p className="text-meta text-ink-muted">{description}</p>
      )}
    </div>
  );
}

function MentionChip({
  label,
  kindLabel,
  icon,
  tone = "neutral",
  variant = "quiet",
  description,
  id,
  peek = true,
  onRemove,
  className,
}: MentionChipProps) {
  const face = (
    <MentionChipFace
      label={label}
      icon={icon}
      tone={tone}
      variant={variant}
      id={id}
      onRemove={onRemove}
      className={className}
    />
  );

  if (!peek) return face;

  return (
    <HoverCard>
      <HoverCardTrigger
        render={
          <span className="inline-flex max-w-full cursor-default align-middle" />
        }
      >
        {face}
      </HoverCardTrigger>
      <HoverCardContent side="top" align="start" sideOffset={6}>
        <MentionChipPeek
          label={label}
          kindLabel={kindLabel}
          icon={icon}
          tone={tone}
          description={description}
        />
      </HoverCardContent>
    </HoverCard>
  );
}

export { MentionChip, MentionChipFace, MentionChipPeek };
export type { MentionChipProps, MentionChipVariant };
