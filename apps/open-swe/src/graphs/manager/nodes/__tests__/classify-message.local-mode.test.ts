import { beforeEach, describe, expect, test, jest } from "@jest/globals";
import { AIMessage, HumanMessage } from "@langchain/core/messages";
import type { BaseLanguageModelInput } from "@langchain/core/language_models/base";
import type {
  ConfigurableChatModelCallOptions,
  ConfigurableModel,
} from "langchain/chat_models/universal";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import type {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import { END } from "@langchain/langgraph";
import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";
import type { Client, Thread } from "@langchain/langgraph-sdk";
import type { loadModel } from "../../../../utils/llms/index.js";
import type { FallbackRunnable } from "../../../../utils/runtime-fallback.js";

type ConfigurableModelInstance = ConfigurableModel<
  BaseLanguageModelInput,
  ConfigurableChatModelCallOptions
>;

type FallbackRunnableInstance = FallbackRunnable<
  BaseLanguageModelInput,
  ConfigurableChatModelCallOptions
>;

type ThreadGet = (
  threadId: string,
) => Promise<Thread<Record<string, unknown>>>;
const threadsGetMock: jest.MockedFunction<ThreadGet> = jest.fn();

type RunsCreate = (
  ...args: Parameters<Client["runs"]["create"]>
) => ReturnType<Client["runs"]["create"]>;
const runsCreateMock: jest.MockedFunction<RunsCreate> = jest.fn();
const createLangGraphClientMock = jest.fn();
const loadModelMock: jest.MockedFunction<typeof loadModel> = jest.fn();
const supportsParallelToolCallsParamMock = jest.fn();

let modelInvokeMock: jest.MockedFunction<ConfigurableModelInstance["invoke"]>;

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

    modelInvokeMock = jest.fn() as jest.MockedFunction<
      ConfigurableModelInstance["invoke"]
    >;

    const modelWithTools: Pick<ConfigurableModelInstance, "invoke"> = {
      invoke: modelInvokeMock,
    };

    const bindToolsMock = jest.fn() as jest.MockedFunction<
      FallbackRunnableInstance["bindTools"]
    >;
    bindToolsMock.mockReturnValue(
      modelWithTools as unknown as ConfigurableModelInstance,
    );

    loadModelMock
      .mockReset()
      .mockResolvedValue(
        {
          bindTools: bindToolsMock,
        } as unknown as FallbackRunnableInstance,
      );
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
    modelInvokeMock.mockResolvedValue(
      responseMessage as Awaited<
        ReturnType<ConfigurableModelInstance["invoke"]>
      >,
    );

    const timestamp = new Date().toISOString();
    threadsGetMock
      .mockResolvedValueOnce(
        {
          thread_id: "planner-thread",
          created_at: timestamp,
          updated_at: timestamp,
          metadata: {},
          status: "busy",
          values: {
            programmerSession: { threadId: "programmer-thread" },
          },
          interrupts: {},
        } satisfies Thread<Record<string, unknown>>,
      )
      .mockResolvedValueOnce(
        {
          thread_id: "programmer-thread",
          created_at: timestamp,
          updated_at: timestamp,
          metadata: {},
          status: "busy",
          values: {},
          interrupts: {},
        } satisfies Thread<Record<string, unknown>>,
      );

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
