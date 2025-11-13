import { FeatureGraph } from "../../feature-graph/graph.js";
import {
  FeatureNode,
  ArtifactRef,
} from "../../feature-graph/types.js";
import {
  featuresForArtifact,
  featuresForTest,
  impactedFeaturesByCodeChange,
  PlanMetadata,
  testsForFeature,
  FeatureMappingOptions,
} from "../../feature-graph/mappings.js";
import { TaskPlan } from "../../open-swe/types.js";

const loginFeature: FeatureNode = {
  id: "feature-login",
  name: "User login flow",
  description: "Handles authentication and credential validation",
  status: "active",
  group: "auth",
  artifacts: [
    { path: "apps/web/src/features/login/__tests__/login.test.ts" },
    { path: "docs/auth/login.md" },
  ],
  metadata: {
    aliases: ["login", "authentication"],
  },
};

const ordersFeature: FeatureNode = {
  id: "feature-orders",
  name: "Orders pipeline",
  description: "Processes incoming purchase orders",
  status: "in-progress",
  artifacts: {
    specification: { path: "docs/orders/pipeline.md" },
  },
  metadata: {
    tags: ["orders", "fulfillment"],
  },
};

const analyticsFeature: FeatureNode = {
  id: "feature-analytics",
  name: "Analytics instrumentation",
  description: "Captures usage metrics across the dashboard",
  status: "proposed",
  artifacts: [],
};

const graph = new FeatureGraph({
  version: 1,
  nodes: new Map<FeatureNode["id"], FeatureNode>([
    [loginFeature.id, loginFeature],
    [ordersFeature.id, ordersFeature],
    [analyticsFeature.id, analyticsFeature],
  ]),
  edges: [],
});

const planMetadata: PlanMetadata = {
  features: {
    "feature-login": {
      tests: "apps/web/src/features/login/login.spec.ts",
      keywords: ["authentication", "login flow"],
    },
    "feature-orders": {
      keywords: "orders pipeline",
    },
  },
  artifacts: {
    "services/orders/tests/order.spec.ts": {
      features: "feature-orders",
      keywords: ["orders", "regression"],
    },
  },
};

const taskPlan: TaskPlan = {
  activeTaskIndex: 0,
  tasks: [
    {
      id: "task-login",
      taskIndex: 0,
      request: "Add regression coverage for feature-login",
      title: "Expand login tests",
      createdAt: 1,
      completed: false,
      featureIds: ["feature-login"],
      planRevisions: [
        {
          revisionIndex: 0,
          plans: [
            {
              index: 0,
              plan: "Update apps/web/src/features/login/__tests__/login.test.ts with happy path coverage",
              completed: false,
              featureIds: ["feature-login"],
            },
            {
              index: 1,
              plan: "Document changes in docs/auth/login.md",
              completed: false,
              featureIds: ["feature-login"],
            },
          ],
          createdAt: 1,
          createdBy: "agent",
        },
      ],
      activeRevisionIndex: 0,
    },
    {
      id: "task-orders",
      taskIndex: 1,
      request: "Create e2e tests for feature-orders",
      title: "Orders e2e tests",
      createdAt: 2,
      completed: false,
      featureIds: ["feature-orders"],
      planRevisions: [
        {
          revisionIndex: 0,
          plans: [
            {
              index: 0,
              plan: "Author services/orders/tests/order.spec.ts to cover the order flow",
              completed: false,
              featureIds: ["feature-orders"],
            },
          ],
          createdAt: 2,
          createdBy: "agent",
        },
      ],
      activeRevisionIndex: 0,
    },
  ],
};

const context: FeatureMappingOptions = {
  planMetadata,
  taskPlan,
};

const extractPaths = (artifacts: ArtifactRef[]): string[] =>
  artifacts
    .map((artifact) =>
      typeof artifact === "string"
        ? artifact
        : artifact.path ?? artifact.url ?? artifact.name ?? "",
    )
    .filter((value): value is string => Boolean(value));

describe("feature graph mappings", () => {
  it("prioritises direct test artifact matches", () => {
    const matches = featuresForTest(
      graph,
      "apps/web/src/features/login/__tests__/login.test.ts",
      context,
    );

    expect(matches.map((feature) => feature.id)).toEqual(["feature-login"]);
  });

  it("uses plan metadata to associate derived test files", () => {
    const matches = featuresForTest(
      graph,
      "apps/web/src/features/login/login.spec.ts",
      context,
    );

    expect(matches.map((feature) => feature.id)).toContain("feature-login");
  });

  it("applies artifact metadata and task context for unmapped tests", () => {
    const matches = featuresForTest(
      graph,
      "services/orders/tests/order.spec.ts",
      context,
    );

    expect(matches.map((feature) => feature.id)).toContain("feature-orders");
  });

  it("aggregates known and inferred tests for a feature", () => {
    const tests = testsForFeature(graph, "feature-login", context);
    expect(extractPaths(tests)).toEqual(
      expect.arrayContaining([
        "apps/web/src/features/login/__tests__/login.test.ts",
        "apps/web/src/features/login/login.spec.ts",
      ]),
    );
  });

  it("detects supporting artifacts for a feature", () => {
    const matches = featuresForArtifact(graph, "docs/auth/login.md", context);
    expect(matches.map((feature) => feature.id)).toEqual(["feature-login"]);
  });

  it("ranks impacted features for a set of code paths", () => {
    const impacted = impactedFeaturesByCodeChange(
      graph,
      [
        "apps/web/src/features/login/__tests__/login.test.ts",
        "services/orders/tests/order.spec.ts",
      ],
      context,
    );

    const impactedIds = impacted.map((feature) => feature.id);
    expect(impactedIds).toHaveLength(2);
    expect(impactedIds).toEqual(
      expect.arrayContaining(["feature-login", "feature-orders"]),
    );
  });
});
