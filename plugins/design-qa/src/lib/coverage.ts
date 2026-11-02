import type { FrontendReviewRun, FrontendScenario } from "@openswe/plugin-sdk"

/** How much of the largest paired state changed: an extent, never a severity. */
export type Extent = "none" | "small" | "moderate" | "large"

export const EXTENT_LABELS: Record<Extent, string> = {
  none: "No changes above threshold",
  small: "Small visual change",
  moderate: "Moderate visual change",
  large: "Large visual change",
}

export function visualExtent(changedRatio: number): Extent {
  if (changedRatio <= 0) return "none"
  if (changedRatio < 0.02) return "small"
  if (changedRatio < 0.15) return "moderate"
  return "large"
}

type Measured = FrontendScenario & {
  metrics: NonNullable<FrontendScenario["metrics"]>
}

/** States compared pixel for pixel: both sides captured, with saved metrics. */
function measuredScenarios(scenarios: readonly FrontendScenario[]): Measured[] {
  return scenarios.filter(
    (scenario): scenario is Measured =>
      scenario.status === "complete" &&
      Boolean(scenario.before_url && scenario.after_url) &&
      scenario.metrics !== null
  )
}

export interface RunOverview {
  total: number
  paired: number
  interactions: number
  /** The paired state that changed most, for the card's thumbnail. */
  largest: Measured | undefined
  extent: Extent | null
}

export function runOverview(run: FrontendReviewRun): RunOverview {
  const scenarios = run.report?.scenarios ?? []
  const measured = measuredScenarios(scenarios)
  const largest = measured.reduce<Measured | undefined>(
    (found, scenario) =>
      !found || scenario.metrics.changed_ratio > found.metrics.changed_ratio
        ? scenario
        : found,
    undefined
  )
  return {
    total: scenarios.length,
    paired: measured.length,
    interactions: measured.filter((scenario) => scenario.interaction).length,
    largest,
    extent: largest ? visualExtent(largest.metrics.changed_ratio) : null,
  }
}
