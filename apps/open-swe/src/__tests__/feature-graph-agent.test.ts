import { describe, expect, it, beforeEach, jest } from "@jest/globals";
import { AIMessage, HumanMessage } from "@langchain/core/messages";
import { FeatureGraph } from "@openswe/shared/feature-graph";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import type {
  FeatureProposalState,
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import * as featureGraphMutations from "../graphs/manager/utils/feature-graph-mutations.js";

const loadModelMock = jest.fn();
const supportsParallelToolCallsParamMock = jest.fn();
jest.mock("../graphs/manager/utils/feature-graph-mutations.js", () => {
  const actual = jest.requireActual(
    "../graphs/manager/utils/feature-graph-mutations.js",
  );
  return { ...actual, persistFeatureGraph: jest.fn() };
});
const persistFeatureGraphMock =
  featureGraphMutations.persistFeatureGraph as jest.MockedFunction<
    typeof featureGraphMutations.persistFeatureGraph
  >;
let featureGraphAgent: typeof import("../graphs/manager/nodes/feature-graph-agent.js")[
  "featureGraphAgent"
];

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
  graph = createGraph(),
  proposals: FeatureProposalState = { proposals: [] },
): ManagerGraphState =>
  ({
    messages: [
      new HumanMessage({ content: "Please manage the feature graph." }),
    ],
    targetRepository: { owner: "acme", repo: "repo" },
    taskPlan: { tasks: [], activeTaskIndex: 0 },
    branchName: "branch",
    autoAcceptPlan: false,
    featureGraph: graph,
    featureProposals: proposals,
    activeFeatureIds: [],
  } as unknown as ManagerGraphState);

const mockModelResponse = (aiMessage: AIMessage) => {
  const invoke = jest.fn().mockResolvedValue(aiMessage);
  const bindTools = jest.fn().mockReturnValue({ invoke });
  loadModelMock.mockResolvedValue({ bindTools });
  supportsParallelToolCallsParamMock.mockReturnValue(false);

  return { invoke, bindTools };
};

describe("featureGraphAgent", () => {
  beforeAll(async () => {
    process.env.SECRETS_ENCRYPTION_KEY = "test-key";
    await jest.unstable_mockModule("../utils/llms/index.js", () => ({
      __esModule: true,
      loadModel: loadModelMock,
      supportsParallelToolCallsParam: supportsParallelToolCallsParamMock,
    }));
    ({ featureGraphAgent } = await import(
      "../graphs/manager/nodes/feature-graph-agent.js"
    ));
  });

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("records proposals and marks features as proposed", async () => {
    const aiMessage = new AIMessage({
      content: "Created proposal",
      tool_calls: [
        {
          id: "call-1",
          name: "propose_feature_change",
          args: {
            featureId: "feature-auth",
            summary: "Add MFA",
            rationale: "Security",
            response: "Proposed MFA for approval.",
          },
          type: "tool_call",
        },
      ],
    });
    mockModelResponse(aiMessage);

    const command = await featureGraphAgent(createState(), config);
    const update = command.update as ManagerGraphUpdate;

    const proposals = update.featureProposals;
    expect(proposals?.proposals).toHaveLength(1);
    const proposal = proposals?.proposals[0];
    expect(proposal?.featureId).toBe("feature-auth");
    expect(proposal?.status).toBe("proposed");
    expect(proposals?.activeProposalId).toBe(proposal?.proposalId);

    const updatedGraph = update.featureGraph;
    expect(updatedGraph?.getFeature("feature-auth")?.status).toBe(
      "proposed",
    );
  });

  it("approves existing proposals and activates the feature", async () => {
    const proposalId = "proposal-1";
    const aiMessage = new AIMessage({
      content: "Approved proposal",
      tool_calls: [
        {
          id: "call-2",
          name: "approve_feature_change",
          args: {
            featureId: "feature-auth",
            proposalId,
            response: "Marked feature as approved.",
          },
          type: "tool_call",
        },
      ],
    });
    mockModelResponse(aiMessage);

    const proposalState: FeatureProposalState = {
      proposals: [
        {
          proposalId,
          featureId: "feature-auth",
          summary: "Enable MFA",
          status: "proposed",
          rationale: "Security",
          updatedAt: new Date().toISOString(),
        },
      ],
      activeProposalId: proposalId,
    };

    const command = await featureGraphAgent(
      createState(createGraph(), proposalState),
      config,
    );
    const update = command.update as ManagerGraphUpdate;

    expect(update.featureProposals?.proposals).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          proposalId,
          status: "approved",
        }),
      ]),
    );
    expect(update.featureProposals?.activeProposalId).toBe(proposalId);
    expect(update.featureGraph?.getFeature("feature-auth")?.status).toBe(
      "active",
    );
  });

  it("records rejections while keeping proposals accessible", async () => {
    const aiMessage = new AIMessage({
      content: "Rejected proposal",
      tool_calls: [
        {
          id: "call-3",
          name: "reject_feature_change",
          args: {
            featureId: "feature-auth",
            proposalId: "proposal-2",
            rationale: "Out of scope",
            response: "Logged rejection.",
          },
          type: "tool_call",
        },
      ],
    });
    mockModelResponse(aiMessage);

    const proposalState: FeatureProposalState = {
      proposals: [
        {
          proposalId: "proposal-2",
          featureId: "feature-auth",
          summary: "Enable MFA",
          status: "proposed",
          rationale: "Security",
          updatedAt: new Date().toISOString(),
        },
      ],
      activeProposalId: "proposal-2",
    };

    const command = await featureGraphAgent(
      createState(createGraph(), proposalState),
      config,
    );
    const update = command.update as ManagerGraphUpdate;

    expect(update.featureProposals?.proposals[0]).toEqual(
      expect.objectContaining({ status: "rejected" }),
    );
    expect(update.featureProposals?.activeProposalId).toBe("proposal-2");
    expect(update.featureGraph?.getFeature("feature-auth")?.status).toBe(
      "rejected",
    );
  });
});
