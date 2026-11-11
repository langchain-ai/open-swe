import type { FrontendReviewRun, FrontendScenario } from "./capture"

/**
 * Paired states: both sides captured. Whether a pair compares shows only on its
 * pull request's page, once its screenshots load.
 */
export function pairedScenarios(
  scenarios: readonly FrontendScenario[]
): FrontendScenario[] {
  return scenarios.filter(
    (scenario) =>
      scenario.status === "complete" &&
      Boolean(scenario.before_url && scenario.after_url)
  )
}

export interface RunOverview {
  total: number
  paired: number
  interactions: number
  /** The first paired state, for the card's thumbnail. */
  cover: FrontendScenario | undefined
}

export function runOverview(run: FrontendReviewRun): RunOverview {
  const scenarios = run.report?.scenarios ?? []
  const paired = pairedScenarios(scenarios)
  return {
    total: scenarios.length,
    paired: paired.length,
    interactions: paired.filter((scenario) => scenario.interaction).length,
    cover: paired[0],
  }
}
