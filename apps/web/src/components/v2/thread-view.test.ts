import { describe, expect, it } from "vitest";

import { shouldAutoGenerateFeatureGraph } from "./thread-view";

describe("shouldAutoGenerateFeatureGraph", () => {
  it("returns false when planner stream is loading", () => {
    expect(
      shouldAutoGenerateFeatureGraph({
        plannerIsLoading: true,
        programmerIsLoading: false,
        hasPlannerSession: false,
        hasProgrammerSession: false,
      }),
    ).toBe(false);
  });

  it("returns false when programmer stream is loading", () => {
    expect(
      shouldAutoGenerateFeatureGraph({
        plannerIsLoading: false,
        programmerIsLoading: true,
        hasPlannerSession: false,
        hasProgrammerSession: false,
      }),
    ).toBe(false);
  });

  it("returns false when a planner or programmer session is active", () => {
    expect(
      shouldAutoGenerateFeatureGraph({
        plannerIsLoading: false,
        programmerIsLoading: false,
        hasPlannerSession: true,
        hasProgrammerSession: false,
      }),
    ).toBe(false);

    expect(
      shouldAutoGenerateFeatureGraph({
        plannerIsLoading: false,
        programmerIsLoading: false,
        hasPlannerSession: false,
        hasProgrammerSession: true,
      }),
    ).toBe(false);
  });

  it("allows auto generation when no sessions are active", () => {
    expect(
      shouldAutoGenerateFeatureGraph({
        plannerIsLoading: false,
        programmerIsLoading: false,
        hasPlannerSession: false,
        hasProgrammerSession: false,
      }),
    ).toBe(true);
  });
});
