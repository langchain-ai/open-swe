import { describe, expect, it, beforeAll, beforeEach, jest } from "@jest/globals";
import { END } from "@langchain/langgraph";
import {
  AIMessage,
  HumanMessage,
  SystemMessage,
} from "@langchain/core/messages";
import { FeatureGraph } from "@openswe/shared/feature-graph";
import { featureGraphFileSchema } from "@openswe/shared/feature-graph/types";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import type {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import type { FeaturePlannerValues } from "../graphs/manager/nodes/feature-graph-orchestrator.js";

const runsWaitMock: jest.MockedFunction<
  (...args: unknown[]) => Promise<FeaturePlannerValues>
> = jest.fn();
let featureGraphOrchestrator: typeof import(
  "../graphs/manager/nodes/feature-graph-orchestrator.js"
)["featureGraphOrchestrator"];
let featurePlannerSystemPrompt: string;

const config = {
  configurable: { workspacePath: "/tmp/workspace" },
} as unknown as GraphConfig;

const createGraph = (): FeatureGraph =>
  new FeatureGraph({
    version: 1,
    nodes: new Map([
      [
        "feature-auth",
        {
          id: "feature-auth",
          name: "Auth",
          description: "Authentication",
          status: "inactive",
        },
      ],
    ]),
    edges: [],
  });

const createState = (
  overrides: Partial<ManagerGraphState> = {},
): ManagerGraphState =>
  ({
    messages: [new HumanMessage({ content: "Please manage the feature graph." })],
    targetRepository: { owner: "acme", repo: "repo" },
    taskPlan: { tasks: [], activeTaskIndex: 0 },
    branchName: "branch",
    autoAcceptPlan: false,
    featureGraph: createGraph(),
    ...overrides,
  } as unknown as ManagerGraphState);

describe("featureGraphOrchestrator", () => {
  beforeAll(async () => {
    process.env.SECRETS_ENCRYPTION_KEY = "test-key";
    await jest.unstable_mockModule("@langchain/langgraph-sdk", () => ({
      Client: jest.fn().mockImplementation(() => ({
        runs: {
          wait: runsWaitMock,
        },
      })),
    }));
    const orchestratorModule = await import(
      "../graphs/manager/nodes/feature-graph-orchestrator.js",
    );
    ({
      featureGraphOrchestrator,
      FEATURE_PLANNER_SYSTEM_PROMPT: featurePlannerSystemPrompt,
    } = orchestratorModule);
  });

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("skips orchestration when the feature graph is missing", async () => {
    const command = await featureGraphOrchestrator(
      createState({ featureGraph: undefined }),
      config,
    );

    expect(command.goto).toContain("classify-message");
    expect(runsWaitMock).not.toHaveBeenCalled();
  });

  it("persists updates returned by the feature planner agent", async () => {
    const plannerGraph = {
      version: 1,
      nodes: [
        {
          id: "feature-auth",
          name: "Auth",
          description: "Authentication",
          status: "active",
        },
      ],
      edges: [],
    };

    expect(featureGraphFileSchema.safeParse(plannerGraph).success).toBe(true);

    runsWaitMock.mockResolvedValueOnce({
      featureGraph: plannerGraph,
      messages: [{ type: "ai", content: "Captured feature update" }],
      activeFeatureIds: ["feature-auth"],
    } as FeaturePlannerValues);

    const command = await featureGraphOrchestrator(createState(), config);
    const update = command.update as ManagerGraphUpdate;

    expect(update?.featureGraph?.getFeature("feature-auth")?.status).toBe(
      "active",
    );
    expect(update?.activeFeatureIds).toEqual(["feature-auth"]);
    expect(update?.messages?.[0]).toBeInstanceOf(AIMessage);
    expect(command.goto).toContain(END);
  });

  it("prepends the planner contract system prompt to the conversation", async () => {
    runsWaitMock.mockResolvedValueOnce({} as FeaturePlannerValues);

    await featureGraphOrchestrator(createState(), config);

    const plannerCall = runsWaitMock.mock.calls[0]?.[2] as
      | { input?: Record<string, unknown> }
      | undefined;
    const messages = plannerCall?.input?.messages as unknown[];

    expect(Array.isArray(messages)).toBe(true);
    const [systemMessage] = messages as unknown[];
    expect(systemMessage).toBeInstanceOf(SystemMessage);
    expect((systemMessage as SystemMessage).content).toContain(
      featurePlannerSystemPrompt,
    );
  });

  it("shares tool deferral and artifact guidance with the planner", async () => {
    runsWaitMock.mockResolvedValueOnce({} as FeaturePlannerValues);

    await featureGraphOrchestrator(createState(), config);

    const plannerCall = runsWaitMock.mock.calls[0]?.[2] as
      | { input?: Record<string, unknown> }
      | undefined;

    expect(plannerCall?.input?.toolingContract).toEqual(
      expect.objectContaining({
        requireStableFeatureId: true,
        requireArtifacts: true,
        artifactGuidance: expect.stringContaining("artifacts"),
        deferToolCallsUntilClarified: true,
      }),
    );
  });
});
