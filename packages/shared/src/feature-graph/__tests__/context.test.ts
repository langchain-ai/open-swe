import { formatFeatureContext } from "../context.js";
import type { FeatureNode } from "../types.js";

describe("formatFeatureContext", () => {
  const baseFeature: FeatureNode = {
    id: "feature-login",
    name: "Login flow",
    description: "Handles credential validation",
    status: "active",
  };
  const dependency: FeatureNode = {
    id: "feature-audit",
    name: "Audit logging",
    description: "Records security events",
    status: "complete",
  };

  it("renders primary and dependency sections", () => {
    const context = formatFeatureContext({
      features: [baseFeature],
      dependencies: [dependency],
    });

    expect(context).toContain("<feature_scope>\\nPrimary features for this request:");
    expect(context).toContain("- Login flow (feature-login) — Handles credential validation — Status: active");
    expect(context).toContain("Upstream dependencies to review:");
    expect(context).toContain("- Audit logging (feature-audit) — Records security events — Status: complete");
    expect(context.trim().endsWith("</feature_scope>")).toBe(true);
  });

  it("omits duplicate dependencies already listed as features", () => {
    const context = formatFeatureContext({
      features: [baseFeature],
      dependencies: [baseFeature, dependency],
    });

    expect(context.match(/feature-login/g)).toHaveLength(1);
    expect(context).toContain("feature-audit");
  });

  it("returns an empty string when no context is available", () => {
    expect(formatFeatureContext({})).toBe("");
    expect(formatFeatureContext({ features: [], dependencies: [] })).toBe("");
  });
});
