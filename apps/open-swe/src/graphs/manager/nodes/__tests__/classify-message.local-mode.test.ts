import { beforeEach, describe, expect, test, jest } from "@jest/globals";
import { AIMessage, HumanMessage } from "@langchain/core/messages";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import type {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import { END } from "@langchain/langgraph";
import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";

const threadsGetMock = jest.fn();
const runsCreateMock = jest.fn();
const createLangGraphClientMock = jest.fn();
const loadModelMock = jest.fn();
const supportsParallelToolCallsParamMock = jest.fn();

let modelInvokeMock: jest.Mock;

await jest.unstable_mockModule(
  "../../../../utils/langgraph-client.js",
  () => ({
    createLangGraphClient: createLangGraphClientMock,
  }),
);

await jest.unstable_mockModule("../../../../utils/llms/index.js", () => ({
  loadModel: loadModelMock,
  supportsParallelToolCallsParam: supportsParallelToolCallsParamMock,
}));

const { classifyMessage } = await import("../classify-message/index.js");

describe("classifyMessage local mode", () => {
  beforeEach(() => {
    threadsGetMock.mockReset();
    runsCreateMock.mockReset();
    createLangGraphClientMock.mockReset().mockReturnValue({
      threads: { get: threadsGetMock },
      runs: { create: runsCreateMock },
    });
    supportsParallelToolCallsParamMock.mockReset().mockReturnValue(false);

    modelInvokeMock = jest.fn();
    const modelWithTools = {
      invoke: modelInvokeMock,
    };

    loadModelMock
      .mockReset()
      .mockResolvedValue({
        bindTools: jest.fn().mockReturnValue(modelWithTools),
      });
  });

  test("routes update_programmer when planner and programmer threads are active in local mode", async () => {
    const responseMessage = new AIMessage({
      id: "router-response",
      content: "update programmer",
      tool_calls: [
        {
          id: "tool-call",
          name: "respond_and_route",
          args: { route: "update_programmer" },
          type: "tool_call",
        },
      ],
    });
    modelInvokeMock.mockResolvedValue(responseMessage);

    threadsGetMock
      .mockResolvedValueOnce({
        status: "in_progress",
        values: {
          programmerSession: { threadId: "programmer-thread" },
        },
      })
      .mockResolvedValueOnce({ status: "in_progress" });

    const userMessage = new HumanMessage({
      id: "human-1",
      content: "Please keep working",
    });

    const state = {
      messages: [userMessage],
      plannerSession: { threadId: "planner-thread" },
      workspacePath: "/tmp/workspace",
      targetRepository: { owner: "owner", repo: "repo" },
      taskPlan: undefined,
    } as unknown as ManagerGraphState;

    const config = {
      configurable: {
        "x-local-mode": "true",
      },
    } as unknown as GraphConfig;

    const command = await classifyMessage(state, config);

    expect(createLangGraphClientMock).toHaveBeenCalledTimes(1);
    expect(createLangGraphClientMock).toHaveBeenCalledWith({
      defaultHeaders: { [LOCAL_MODE_HEADER]: "true" },
    });

    expect(threadsGetMock).toHaveBeenNthCalledWith(1, "planner-thread");
    expect(threadsGetMock).toHaveBeenNthCalledWith(2, "programmer-thread");

    expect(modelInvokeMock).toHaveBeenCalledTimes(1);

    expect(command.goto).toEqual([END]);
    const update = command.update as ManagerGraphUpdate;
    expect(update.workspacePath).toBe(state.workspacePath);
    expect(update.messages).toEqual([responseMessage]);
    expect(update.issueId).toBeUndefined();
  });
});
