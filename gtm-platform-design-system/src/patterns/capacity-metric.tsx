import type { ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";
import { Frame, FramePanel } from "../ui/frame";
import type { Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { IconWell } from "../ui/icon-well";

const CAPACITY_METRIC_RULES: readonly string[] = [
  "Capacity is a remaining allowance, not usage completed. The dominant number says what is left and the meter fills in the same direction.",
  "A capacity set is one ReUI Frame with one hairline outline, no mat gutter or nested panel border, and repeated metric cells. Each cell has one icon, one clear title, one available count, and one segmented meter.",
  "The caller owns every label and number. The pattern clamps the meter for presentation but never derives a provider limit, reset window, or business meaning.",
  "A zero balance is a fact and uses the risk tone. Missing or unreadable data says Unavailable and never renders as zero.",
  "Numeric limits and secondary windows belong in the containing surface's help. A next-available time may sit below the meter when capacity is zero.",
  "Capacity metrics are readings, not controls. Navigation, help, refresh, and mutation affordances belong to the containing product surface.",
];

const CAPACITY_SEGMENTS = 20;

type CapacityTone = "neutral" | "attention" | "risk";

function getCapacityTone(remaining: number, limit: number): CapacityTone {
  if (remaining <= 0) return "risk";
  return limit > 0 && remaining / limit <= 0.2 ? "attention" : "neutral";
}

function capacityToneClass(tone: CapacityTone): string {
  if (tone === "risk") return "text-risk";
  if (tone === "attention") return "text-attention";
  return "text-ink";
}

interface CapacityMeterProps {
  /** Accessible metric and window label. */
  label: string;
  /** Remaining capacity. Clamped only for meter presentation. */
  remaining: number;
  /** Maximum capacity for the displayed window. */
  limit: number;
  /** Optional semantic tone override. */
  tone?: CapacityTone;
}

/** ReUI chart-28's discrete gauge, normalized as the shared capacity primitive. */
function CapacityMeter({ label, limit, remaining, tone }: CapacityMeterProps) {
  const safeLimit = Math.max(0, limit);
  const safeRemaining = Math.max(0, Math.min(remaining, safeLimit));
  const filled =
    safeLimit === 0 || safeRemaining === 0
      ? 0
      : Math.max(
          1,
          Math.round((safeRemaining / safeLimit) * CAPACITY_SEGMENTS)
        );
  const resolvedTone = tone ?? getCapacityTone(safeRemaining, safeLimit);

  return (
    <Box
      data-slot="capacity-meter"
      className="flex h-3 w-full gap-1"
      role="img"
      aria-label={`${label}: ${safeRemaining} of ${safeLimit} remaining`}
    >
      {Array.from({ length: CAPACITY_SEGMENTS }, (_, index) => (
        <Box
          key={index}
          render={<span />}
          aria-hidden="true"
          className={cn(
            "min-w-0 flex-1 rounded-sm border",
            index < filled &&
              resolvedTone === "neutral" &&
              "border-primary bg-primary",
            index < filled &&
              resolvedTone === "attention" &&
              "border-attention bg-attention",
            index < filled &&
              resolvedTone === "risk" &&
              "border-risk bg-risk",
            index >= filled && "border-line bg-muted"
          )}
        />
      ))}
    </Box>
  );
}

interface CapacityMetricProps {
  /** Clear metric name. */
  title: string;
  /** Product glyph shown in the standard icon well. */
  icon: Glyph;
  /** Remaining capacity, omitted only when unavailable. */
  remaining?: number;
  /** Maximum capacity for the primary window, omitted only when unavailable. */
  limit?: number;
  /** Accessible metric and window label for the meter. */
  meterLabel?: string;
  /** Optional next-available time when capacity is zero. */
  detail?: ReactNode;
  /** Explicitly marks this reading unavailable. */
  unavailable?: boolean;
  /** Optional semantic tone override. */
  tone?: CapacityTone;
}

function CapacityMetric({
  detail,
  icon,
  limit,
  meterLabel,
  remaining,
  title,
  tone,
  unavailable = false,
}: CapacityMetricProps) {
  const hasReading =
    !unavailable &&
    remaining !== undefined &&
    limit !== undefined &&
    meterLabel !== undefined;
  const resolvedTone =
    hasReading && remaining !== undefined && limit !== undefined
      ? tone ?? getCapacityTone(remaining, limit)
      : "neutral";

  return (
    <Stack
      render={<li />}
      data-slot="capacity-metric"
      gap="sm"
      className="min-w-0 p-3"
    >
      <Inline gap="sm" align="center">
        <IconWell className="size-7 rounded-control text-ink-subtle">
          <Icon icon={icon} size="md" />
        </IconWell>
        <Box
          render={<h3 />}
          className="min-w-0 truncate text-label font-medium text-ink"
        >
          {title}
        </Box>
      </Inline>
      {hasReading && remaining !== undefined && limit !== undefined ? (
        <>
          <Stack gap="xs">
            <Inline gap="xs" align="baseline">
              <Box
                render={<span />}
                className={cn(
                  "text-title font-semibold tabular-nums",
                  capacityToneClass(resolvedTone)
                )}
              >
                {remaining}
              </Box>
              <Box render={<span />} className="text-meta text-ink-subtle tabular-nums">
                available
              </Box>
            </Inline>
          </Stack>
          <CapacityMeter
            label={meterLabel}
            remaining={remaining}
            limit={limit}
            tone={resolvedTone}
          />
          {detail}
        </>
      ) : (
        <Box render={<span />} className="text-meta text-ink-muted">
          Unavailable
        </Box>
      )}
    </Stack>
  );
}

interface CapacityMetricGridProps {
  /** Accessible name for the capacity set. */
  label: string;
  /** Repeated CapacityMetric elements. */
  children: ReactNode;
  columns?: 1 | 2;
}

function CapacityMetricGrid({ children, label, columns = 2 }: CapacityMetricGridProps) {
  return (
    <Frame
      data-slot="capacity-metric-grid"
      dense
      stacked
      spacing="sm"
      className="w-full"
    >
      <FramePanel className="p-0">
        <Box
          render={<ul />}
          aria-label={label}
          className={columns === 1
            ? "grid grid-cols-1"
            : "grid grid-cols-2 [&>[data-slot=capacity-metric]:nth-child(odd)]:border-r [&>[data-slot=capacity-metric]:nth-child(-n+2)]:border-b [&>[data-slot=capacity-metric]]:border-line"}
        >
          {children}
        </Box>
      </FramePanel>
    </Frame>
  );
}

export {
  CapacityMeter,
  CapacityMetric,
  CapacityMetricGrid,
  CAPACITY_METRIC_RULES,
  getCapacityTone,
};
export type {
  CapacityMeterProps,
  CapacityMetricGridProps,
  CapacityMetricProps,
  CapacityTone,
};
