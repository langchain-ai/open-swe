import { beforeAll, beforeEach, describe, expect, it, jest } from "@jest/globals";
import { AIMessage, HumanMessage } from "@langchain/core/messages";
import { FeatureGraph } from "@openswe/shared/feature-graph";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import type { ManagerGraphState, ManagerGraphUpdate } from "@openswe/shared/open-swe/manager/types";
import type { createLangGraphClient as createLangGraphClientType } from "../../../../utils/langgraph-client.js";
import type {
  loadModel as loadModelType,
  supportsParallelToolCallsParam as supportsParallelToolCallsParamType,
} from "../../../../utils/llms/index.js";

const invokeMock = jest.fn<() => Promise<AIMessage>>();
const bindToolsMock = jest.fn().mockImplementation((_tools, _options) => ({
  invoke: invokeMock,
}));
const loadModelMock = jest.fn<typeof loadModelType>();
const supportsParallelToolCallsParamMock = jest.fn<typeof supportsParallelToolCallsParamType>();
const createLangGraphClientMock = jest.fn<typeof createLangGraphClientType>();

let classifyMessage: typeof import("../classify-message/index.js")["classifyMessage"];

const config = {
  configurable: { "x-local-mode": "true" },
} as unknown as GraphConfig;

const createFeatureGraph = (status = "inactive") =>
  new FeatureGraph({
    version: 1,
    nodes: new Map([
      [
        "feature-auth",
        {
          id: "feature-auth",
          name: "Auth",
          description: "Authentication",
          status,
        },
      ],
    ]),
    edges: [],
  });

const createState = (
  overrides: Partial<ManagerGraphState> = {},
): ManagerGraphState => ({
  messages: [
    new HumanMessage({
      content: "Please plan the selected feature",
      additional_kwargs: { requestSource: "open-swe" },
    }),
  ],
  targetRepository: { owner: "acme", repo: "repo" },
  taskPlan: {
    tasks: [
      {
        id: "task-1",
        taskIndex: 0,
        request: "Please plan the selected feature",
        title: "Initial task",
        createdAt: Date.now(),
        completed: false,
        planRevisions: [
          {
            revisionIndex: 0,
            plans: [
              { index: 0, plan: "Do work", completed: false },
            ],
            createdAt: Date.now(),
            createdBy: "agent",
          },
        ],
        activeRevisionIndex: 0,
      },
    ],
    activeTaskIndex: 0,
  },
  branchName: "branch",
  featureGraph: createFeatureGraph(),
  ...overrides,
});

describe("classifyMessage", () => {
  beforeAll(async () => {
    loadModelMock.mockResolvedValue(
      { bindTools: bindToolsMock } as unknown as Awaited<
        ReturnType<typeof loadModelType>
      >,
    );
    supportsParallelToolCallsParamMock.mockReturnValue(false);
    createLangGraphClientMock.mockReturnValue({
      threads: { get: jest.fn() },
    } as unknown as ReturnType<typeof createLangGraphClientType>);

    await jest.unstable_mockModule("../../../../utils/llms/index.js", () => ({
      loadModel: loadModelMock,
      supportsParallelToolCallsParam: supportsParallelToolCallsParamMock,
    }));

    await jest.unstable_mockModule("../../../../utils/langgraph-client.js", () => ({
      createLangGraphClient: createLangGraphClientMock,
    }));

    const module = await import("../classify-message/index.js");
    classifyMessage = module.classifyMessage;
  });

  beforeEach(() => {
    jest.clearAllMocks();
    invokeMock.mockResolvedValue(
      new AIMessage({
        content: "Routing to planner",
        tool_calls: [
          {
            name: "respond_and_route",
            args: { route: "start_planner", response: "Starting" },
          },
        ],
      }),
    );
  });

  it("routes the user back to feature orchestration when approval is missing", async () => {
    const command = await classifyMessage(createState(), config);
    const commandUpdate = command.update as ManagerGraphUpdate | undefined;

    expect(command.goto).toContain("feature-graph-orchestrator");
    const reminder = commandUpdate?.messages?.[0];
    expect(reminder).toBeInstanceOf(AIMessage);
    expect((reminder as AIMessage).content).toContain("approve the active feature");
  });

  it("allows planner routing once feature approval is present", async () => {
    const command = await classifyMessage(
      createState({
        activeFeatureIds: ["feature-auth"],
        featureGraph: createFeatureGraph("active"),
      }),
      config,
    );
    const commandUpdate = command.update as ManagerGraphUpdate | undefined;

    expect(command.goto).toContain("start-planner");
    expect(commandUpdate?.userHasApprovedFeature).toBe(true);
  });
});
