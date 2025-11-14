import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import type { GraphConfig, GraphState } from "@openswe/shared/open-swe/types";
import type {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@openswe/shared/open-swe/planner/types";
import type { TaskPlan } from "@openswe/shared/open-swe/types";
import { prepareGraphState } from "../graphs/planner/nodes/prepare-state.js";
import {
  resolveActiveFeatures,
  resolveFeatureDependencies,
} from "../graphs/planner/utils/feature-graph.js";
import { collectFeatureGuidance, formatFeatureGuidance } from "../graphs/programmer/utils/feature-guidance.js";

type WorkspaceFixture = {
  workspacePath: string;
  cleanup: () => Promise<void>;
};

const createWorkspaceWithGraph = async (graphYaml: string): Promise<WorkspaceFixture> => {
  const workspacePath = await mkdtemp(path.join(tmpdir(), "feature-flow-"));
  const graphDir = path.join(workspacePath, "features", "graph");
  await mkdir(graphDir, { recursive: true });
  await writeFile(path.join(graphDir, "graph.yaml"), graphYaml, "utf8");
  return {
    workspacePath,
    cleanup: () => rm(workspacePath, { recursive: true, force: true }),
  };
};

const createTaskPlan = (featureId: string): TaskPlan => ({
  activeTaskIndex: 0,
  tasks: [
    {
      id: "task-1",
      taskIndex: 0,
      request: `Deliver updates for ${featureId}`,
      title: "Implement feature work",
      createdAt: Date.now(),
      completed: false,
      planRevisions: [
        {
          revisionIndex: 0,
          createdAt: Date.now(),
          createdBy: "agent",
          plans: [
            {
              index: 0,
              plan: "Update core implementation",
              completed: false,
              featureIds: [featureId],
            },
          ],
        },
      ],
      activeRevisionIndex: 0,
      featureIds: [featureId],
    },
  ],
});

describe("feature context integration", () => {
  it("propagates planner feature context into programmer guidance", async () => {
    const graphYaml = `version: 1
nodes:
  - id: feature-alpha
    name: Authentication overhaul
    description: Refreshes login UX
    status: active
    artifacts:
      - apps/web/src/auth/__tests__/login.test.ts
      - docs/features/authentication.md
  - id: feature-beta
    name: Billing notifications
    description: Adds payment alerts
    status: in-progress
    artifacts:
      - apps/api/tests/billing.int.test.ts
      - docs/features/billing.md
edges:
  - source: feature-alpha
    target: feature-beta
    type: upstream
`;

    const { workspacePath, cleanup } = await createWorkspaceWithGraph(graphYaml);

    try {
      const config = {
        configurable: { workspacePath, shouldCreateIssue: false },
        thread_id: "thread-id",
        assistant_id: "planner",
      } as unknown as GraphConfig;

      const taskPlan = createTaskPlan("feature-beta");

      const plannerState = {
        messages: [],
        internalMessages: [],
        sandboxSessionId: "sandbox-1",
        targetRepository: { owner: "acme", repo: "demo" },
        workspacePath,
        features: [],
        featureDependencies: [],
        activeFeatureIds: ["feature-beta"],
        issueId: undefined,
        codebaseTree: "",
        documentCache: {},
        taskPlan,
        proposedPlan: [],
        contextGatheringNotes: "",
        branchName: "main",
        planChangeRequest: "",
        programmerSession: { threadId: "prog-thread", runId: "run" },
        proposedPlanTitle: "",
        customRules: undefined,
        autoAcceptPlan: undefined,
        tokenData: undefined,
      } as unknown as PlannerGraphState;

      const command = await prepareGraphState(plannerState, config);
      const update = command.update as PlannerGraphUpdate;

      expect(update.features?.map((feature) => feature.id)).toEqual([
        "feature-beta",
      ]);
      expect(update.featureDependencies?.map((feature) => feature.id)).toEqual([
        "feature-alpha",
      ]);

      const programmerState = {
        messages: [],
        internalMessages: [],
        workspacePath,
        features: update.features,
        featureDependencies: update.featureDependencies,
        taskPlan,
        contextGatheringNotes: "",
        sandboxSessionId: "sandbox-1",
        branchName: "main",
        documentCache: {},
        dependenciesInstalled: false,
        reviewsCount: 0,
      } as unknown as GraphState;

      const guidance = await collectFeatureGuidance(programmerState, config);
      const formatted = formatFeatureGuidance(guidance);

      expect(guidance.features?.map((feature) => feature.id)).toEqual([
        "feature-beta",
      ]);
      expect(guidance.dependencies?.map((feature) => feature.id)).toEqual([
        "feature-alpha",
      ]);
      expect(guidance.testHints).toEqual([
        "apps/api/tests/billing.int.test.ts",
        "apps/web/src/auth/__tests__/login.test.ts",
      ]);
      expect(guidance.artifactHints).toEqual([
        "apps/api/tests/billing.int.test.ts",
        "docs/features/billing.md",
        "apps/web/src/auth/__tests__/login.test.ts",
        "docs/features/authentication.md",
      ]);

      expect(formatted).toContain("<feature_scope>");
      expect(formatted).toContain("feature-beta");
      expect(formatted).toContain("feature-alpha");
      expect(formatted).toContain("<feature_tests>");
    } finally {
      await cleanup();
    }
  });

  it("reuses cached planner graph data across successive resolutions", async () => {
    const graphYaml = `version: 1
nodes:
  - id: feature-core
    name: Core platform
    description: Foundational services
    status: active
  - id: feature-ui
    name: UI polish
    description: Improves layout
    status: proposed
edges:
  - source: feature-core
    target: feature-ui
    type: depends-on
`;

    const { workspacePath, cleanup } = await createWorkspaceWithGraph(graphYaml);

    try {
      const firstResolution = await resolveActiveFeatures({
        workspacePath,
        featureIds: ["feature-ui"],
      });
      expect(firstResolution.map((feature) => feature.id)).toEqual([
        "feature-ui",
      ]);

      const secondResolution = await resolveActiveFeatures({
        workspacePath,
        featureIds: ["feature-ui", "feature-core"],
      });
      expect(secondResolution.map((feature) => feature.id)).toEqual([
        "feature-ui",
        "feature-core",
      ]);

      const dependencies = await resolveFeatureDependencies({
        workspacePath,
        featureIds: ["feature-ui"],
      });
      expect(dependencies.map((feature) => feature.id)).toEqual([
        "feature-core",
      ]);
    } finally {
      await cleanup();
    }
  });
});
