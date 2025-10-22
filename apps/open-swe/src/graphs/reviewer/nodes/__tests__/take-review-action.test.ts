import { beforeEach, describe, expect, test, jest } from "@jest/globals";
import { AIMessage, isAIMessage } from "@langchain/core/messages";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import type {
  ReviewerGraphState,
  ReviewerGraphUpdate,
} from "@openswe/shared/open-swe/reviewer/types";

type ToolInvokeResult = {
  result: string;
  status: "success" | "error";
};

type FilterUnsafeCommandsResult = {
  filteredToolCalls: unknown[];
  wasFiltered: boolean;
};

type SandboxResult = {
  sandbox: { id: string };
  codebaseTree: unknown;
  dependenciesInstalled: unknown;
};

const shellInvokeMock =
  jest.fn<(args: unknown) => Promise<ToolInvokeResult>>();
const installDependenciesInvokeMock =
  jest.fn<(args: unknown) => Promise<ToolInvokeResult>>();
const grepInvokeMock = jest.fn<(args: unknown) => Promise<ToolInvokeResult>>();
const viewInvokeMock = jest.fn<(args: unknown) => Promise<ToolInvokeResult>>();
const scratchpadInvokeMock =
  jest.fn<(args: unknown) => Promise<ToolInvokeResult>>();
const filterUnsafeCommandsMock =
  jest.fn<
    (toolCalls: unknown, config: GraphConfig) =>
      Promise<FilterUnsafeCommandsResult>
  >();
const getSandboxWithErrorHandlingMock =
  jest.fn<
    (
      sandboxSessionId: unknown,
      repository: unknown,
      branch: unknown,
      config: GraphConfig,
    ) => Promise<SandboxResult>
  >();

await jest.unstable_mockModule("../../../../tools/index.js", () => ({
  createShellTool: () => ({
    name: "shell",
    invoke: shellInvokeMock,
  }),
  createInstallDependenciesTool: () => ({
    name: "install_dependencies",
    invoke: installDependenciesInvokeMock,
  }),
}));

await jest.unstable_mockModule("../../../../tools/grep.js", () => ({
  createGrepTool: () => ({
    name: "grep",
    invoke: grepInvokeMock,
  }),
}));

await jest.unstable_mockModule("../../../../tools/builtin-tools/view.js", () => ({
  createViewTool: () => ({
    name: "view",
    invoke: viewInvokeMock,
  }),
}));

await jest.unstable_mockModule("../../../../tools/scratchpad.js", () => ({
  createScratchpadTool: () => ({
    name: "scratchpad",
    invoke: scratchpadInvokeMock,
  }),
}));

await jest.unstable_mockModule("../../../../utils/command-evaluation.js", () => ({
  filterUnsafeCommands: filterUnsafeCommandsMock,
}));

await jest.unstable_mockModule("../../../../utils/sandbox.js", () => ({
  getSandboxWithErrorHandling: getSandboxWithErrorHandlingMock,
}));

const { takeReviewerActions } = await import("../take-review-action.js");
const { filterHiddenMessages } = await import("../../../../utils/message/filter-hidden.js");

describe("takeReviewerActions", () => {
  beforeEach(() => {
    shellInvokeMock.mockReset().mockResolvedValue({
      result: "shell",
      status: "success",
    });
    installDependenciesInvokeMock.mockReset().mockResolvedValue({
      result: "install",
      status: "success",
    });
    grepInvokeMock.mockReset().mockResolvedValue({
      result: "grep",
      status: "success",
    });
    viewInvokeMock.mockReset().mockResolvedValue({
      result: "view",
      status: "success",
    });
    scratchpadInvokeMock.mockReset().mockResolvedValue({
      result: "scratchpad",
      status: "success",
    });
    filterUnsafeCommandsMock.mockReset().mockResolvedValue({
      filteredToolCalls: [],
      wasFiltered: true,
    });
    getSandboxWithErrorHandlingMock.mockReset().mockResolvedValue({
      sandbox: { id: "sandbox-id" },
      codebaseTree: null,
      dependenciesInstalled: null,
    });
  });

  test("marks filtered commands as hidden in reviewer history", async () => {
    const unsafeToolCall = {
      id: "call-1",
      name: "shell",
      args: { command: ["rm", "-rf", "/"], workdir: "." },
    };

    const lastMessage = new AIMessage({
      id: "ai-message-1",
      content: "",
      tool_calls: [unsafeToolCall],
    });

    const state = {
      reviewerMessages: [lastMessage],
      internalMessages: [],
      sandboxSessionId: "sandbox-id",
      targetRepository: { owner: "owner", repo: "repo" },
      branchName: "main",
      codebaseTree: "",
      taskPlan: { tasks: [], activeTaskIndex: 0 },
      dependenciesInstalled: false,
    } as unknown as ReviewerGraphState;

    const config = {
      configurable: { "x-local-mode": "true" },
      thread_id: "thread-id",
      assistant_id: "assistant-id",
      callbacks: [],
      metadata: {},
      tags: [],
    } as unknown as GraphConfig;

    const command = await takeReviewerActions(state, config);
    const update = command.update as ReviewerGraphUpdate;

    expect(filterUnsafeCommandsMock).toHaveBeenCalledTimes(1);

    const reviewerMessagesUpdate = update.reviewerMessages ?? [];
    expect(reviewerMessagesUpdate.length).toBeGreaterThan(0);

    const mergedMessages = [
      ...state.reviewerMessages.slice(0, -1),
      ...reviewerMessagesUpdate,
    ];

    const outboundHistory = filterHiddenMessages(mergedMessages);

    expect(outboundHistory).toHaveLength(1);
    const [aiHistoryMessage] = outboundHistory;
    expect(isAIMessage(aiHistoryMessage)).toBe(true);
    if (isAIMessage(aiHistoryMessage)) {
      expect(aiHistoryMessage.id).toBe(lastMessage.id);
      expect(aiHistoryMessage.tool_calls?.length ?? 0).toBe(0);
    }
  });
});
