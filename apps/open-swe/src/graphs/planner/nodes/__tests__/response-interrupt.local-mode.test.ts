import { beforeEach, describe, expect, test, jest } from "@jest/globals";
import { HumanMessage, isHumanMessage } from "@langchain/core/messages";
import type { BindToolsInput } from "@langchain/core/language_models/chat_models";
import type {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@openswe/shared/open-swe/planner/types";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import type { LangGraphModule } from "../../../../types/langgraph.js";
import type {
  loadModel,
  supportsParallelToolCallsParam,
} from "../../../../utils/llms/index.js";
import type { trackCachePerformance } from "../../../../utils/caching.js";
import type {
  ModelManager,
  getModelManager,
} from "../../../../utils/llms/model-manager.js";

type PlannerToolCallResult = {
  tool_calls: Array<{
    args: {
      reasoning: string;
      decision: string;
    };
  }>;
};

type InvokeFn = (
  input: unknown,
  options?: Record<string, unknown>,
) => Promise<PlannerToolCallResult>;

type BindToolsFn = (
  tools: BindToolsInput[],
  kwargs?: Record<string, unknown>,
) => {
  invoke: InvokeFn;
};

type LoadModelMockFn = (
  ...args: Parameters<typeof loadModel>
) => Promise<{ bindTools: BindToolsFn }>;

type SupportsParallelToolCallsParamFn = typeof supportsParallelToolCallsParam;

type TrackCachePerformanceFn = typeof trackCachePerformance;

type GetModelManagerFn = typeof getModelManager;

const invokeMock = jest.fn<InvokeFn>();
const bindToolsMock = jest.fn<BindToolsFn>();
const loadModelMock = jest.fn<LoadModelMockFn>();
const supportsParallelToolCallsParamMock = jest.fn<
  SupportsParallelToolCallsParamFn
>();
const trackCachePerformanceMock = jest.fn<TrackCachePerformanceFn>();
const getModelManagerMock = jest.fn<GetModelManagerFn>();
const interruptMock = jest.fn<LangGraphModule["interrupt"]>();

const getModelNameForTaskMock = jest.fn<ModelManager["getModelNameForTask"]>();

const modelManagerStub = {
  getModelNameForTask: getModelNameForTaskMock,
} as unknown as ModelManager;

await jest.unstable_mockModule("@langchain/langgraph", () => {
  const actual = jest.requireActual<LangGraphModule>("@langchain/langgraph");
  return {
    ...actual,
    interrupt: interruptMock,
  };
});

await jest.unstable_mockModule("../../../../utils/llms/index.js", () => ({
  loadModel: loadModelMock,
  supportsParallelToolCallsParam: supportsParallelToolCallsParamMock,
}));

await jest.unstable_mockModule("../../../../utils/caching.js", () => ({
  trackCachePerformance: trackCachePerformanceMock,
}));

await jest.unstable_mockModule(
  "../../../../utils/llms/model-manager.js",
  () => ({
    getModelManager: getModelManagerMock,
  }),
);

const { Command } = await import("@langchain/langgraph");
const { interruptProposedPlan } = await import("../proposed-plan.js");
const { determineNeedsContext } = await import("../determine-needs-context.js");

describe("planner interrupt flow", () => {
  beforeEach(() => {
    interruptMock.mockReset().mockReturnValue({
      type: "response",
      args: "Please add more detail to step 2.",
    });

    invokeMock.mockReset().mockResolvedValue({
      tool_calls: [
        {
          args: {
            reasoning: "The existing context is sufficient.",
            decision: "have_context",
          },
        },
      ],
    });

    bindToolsMock.mockReset().mockReturnValue({
      invoke: invokeMock,
    });

    loadModelMock.mockReset().mockResolvedValue({
      bindTools: bindToolsMock,
    });

    supportsParallelToolCallsParamMock
      .mockReset()
      .mockReturnValue(false);
    trackCachePerformanceMock.mockReset().mockReturnValue([]);
    getModelNameForTaskMock.mockReset().mockReturnValue("test-model");
    getModelManagerMock.mockReset().mockReturnValue(modelManagerStub);
  });

  test("continues planning after response interrupt without missing messages", async () => {
    const initialMessage = new HumanMessage({
      content: "Initial request details",
      additional_kwargs: { isOriginalIssue: true },
    });

    const state = {
      messages: [initialMessage],
      internalMessages: [],
      sandboxSessionId: "sandbox-id",
      targetRepository: { owner: "owner", repo: "repo" },
      workspacePath: undefined,
      issueId: undefined,
      codebaseTree: "",
      documentCache: {},
      taskPlan: { tasks: [], activeTaskIndex: 0 },
      proposedPlan: ["Do something"],
      contextGatheringNotes: "",
      branchName: "main",
      planChangeRequest: "",
      programmerSession: { threadId: "", runId: "" },
      proposedPlanTitle: "Plan title",
      autoAcceptPlan: false,
    } as unknown as PlannerGraphState;

    const config = {
      configurable: { shouldCreateIssue: false },
      thread_id: "thread-id",
      assistant_id: "assistant-id",
      callbacks: [],
      metadata: {},
      tags: [],
    } as unknown as GraphConfig;

    const interruptCommand = await interruptProposedPlan(state, config);
    expect(interruptCommand).toBeInstanceOf(Command);

    const update = interruptCommand.update as PlannerGraphUpdate;
    expect(update.planChangeRequest).toBe(
      "Please add more detail to step 2.",
    );
    expect(update.messages).toHaveLength(1);
    const firstMessage = update.messages?.[0];
    if (!firstMessage) {
      throw new Error("Expected first message to be defined");
    }
    expect(isHumanMessage(firstMessage)).toBe(true);

    const updatedState: PlannerGraphState = {
      ...state,
      planChangeRequest: update.planChangeRequest ?? state.planChangeRequest,
      messages: [...state.messages, ...(update.messages ?? [])],
    };

    const determineCommand = await determineNeedsContext(
      updatedState,
      config,
    );

    expect(determineCommand).toBeInstanceOf(Command);
    expect(loadModelMock).toHaveBeenCalledTimes(1);
    expect(bindToolsMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(trackCachePerformanceMock).toHaveBeenCalledTimes(1);
  });
});
