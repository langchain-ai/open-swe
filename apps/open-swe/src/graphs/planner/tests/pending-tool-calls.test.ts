import { beforeEach, describe, expect, jest, test } from "@jest/globals";
import { AIMessage, ToolMessage } from "@langchain/core/messages";
import { Command, END } from "@langchain/langgraph";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import type { PlannerGraphState } from "@openswe/shared/open-swe/planner/types";

const prepareGraphStateMock = jest.fn();
const generateActionMock = jest.fn();
const takeActionsMock = jest.fn();
const generatePlanMock = jest.fn();
const notetakerMock = jest.fn();
const interruptProposedPlanMock = jest.fn();
const determineNeedsContextMock = jest.fn();
const diagnoseErrorMock = jest.fn();
const initializeSandboxMock = jest.fn();

await jest.unstable_mockModule("../nodes/index.js", () => ({
  prepareGraphState: prepareGraphStateMock,
  generateAction: generateActionMock,
  takeActions: takeActionsMock,
  generatePlan: generatePlanMock,
  notetaker: notetakerMock,
  interruptProposedPlan: interruptProposedPlanMock,
  determineNeedsContext: determineNeedsContextMock,
}));

await jest.unstable_mockModule("../../shared/initialize-sandbox.js", () => ({
  initializeSandbox: initializeSandboxMock,
}));

await jest.unstable_mockModule("../../shared/diagnose-error.js", () => ({
  diagnoseError: diagnoseErrorMock,
}));

const { graph } = await import("../index.js");

const basePlannerState = {
  sandboxSessionId: "sandbox-id",
  targetRepository: { owner: "acme", repo: "demo" },
  workspacePath: "",
  features: [],
  featureDependencies: [],
  activeFeatureIds: [],
  issueId: undefined,
  codebaseTree: "",
  documentCache: {},
  taskPlan: { tasks: [], activeTaskIndex: 0 },
  proposedPlan: [],
  contextGatheringNotes: "",
  branchName: "main",
  planChangeRequest: "",
  programmerSession: { threadId: "thread", runId: "run" },
  proposedPlanTitle: "",
  customRules: undefined,
  autoAcceptPlan: undefined,
  tokenData: undefined,
  internalMessages: [],
} satisfies Partial<PlannerGraphState>;

const graphConfig = {
  configurable: { shouldCreateIssue: false },
  thread_id: "thread-id",
  assistant_id: "planner",
  callbacks: [],
  metadata: {},
  tags: [],
} as unknown as GraphConfig;

describe("planner graph pending tool calls", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    prepareGraphStateMock.mockResolvedValue(
      new Command({ goto: "initialize-sandbox", update: {} }),
    );
    initializeSandboxMock.mockImplementation(async (state: PlannerGraphState) => ({
      ...state,
      messages: [
        ...state.messages,
        new AIMessage({
          id: "init-message",
          content: "init",
          additional_kwargs: { hidden: true },
        }),
      ],
    }));
    takeActionsMock.mockResolvedValue(
      new Command({
        goto: "generate-plan",
        update: {
          messages: [
            new ToolMessage({
              id: "tool-message",
              tool_call_id: "call-1",
              content: "tool complete",
              name: "shell",
              status: "success",
            }),
          ],
        },
      }),
    );
    generateActionMock.mockResolvedValue({
      messages: [new AIMessage({ id: "model-message", content: "model" })],
    });
    generatePlanMock.mockResolvedValue({
      messages: [new AIMessage({ id: "plan-message", content: "plan" })],
    });
    notetakerMock.mockResolvedValue({ messages: [] });
    interruptProposedPlanMock.mockResolvedValue(
      new Command({ goto: END, update: {} }),
    );
    determineNeedsContextMock.mockResolvedValue(
      new Command({ goto: "generate-plan", update: {} }),
    );
    diagnoseErrorMock.mockResolvedValue({});
  });

  test("executes tool actions before triggering a new planner model call", async () => {
    const state = {
      ...basePlannerState,
      messages: [
        new AIMessage({
          id: "ai-with-tool",
          content: "",
          tool_calls: [
            {
              id: "call-1",
              name: "shell",
              args: { command: "echo hi", workdir: "." },
            },
          ],
        }),
      ],
    } as PlannerGraphState;

    await graph.invoke(state, graphConfig);

    expect(takeActionsMock).toHaveBeenCalledTimes(1);
    expect(generateActionMock).not.toHaveBeenCalled();

    const [takeActionsState] = takeActionsMock.mock.calls[0];
    expect(takeActionsState.messages.at(-1)).toBeInstanceOf(AIMessage);
  });

  test("routes to planner generation when no pending tool calls exist", async () => {
    const state = {
      ...basePlannerState,
      messages: [
        new AIMessage({ id: "ai-no-tools", content: "ready" }),
        new AIMessage({
          id: "ai-hidden",
          content: "background",
          additional_kwargs: { hidden: true },
        }),
      ],
    } as PlannerGraphState;

    await graph.invoke(state, graphConfig);

    expect(generateActionMock).toHaveBeenCalledTimes(1);
    expect(takeActionsMock).not.toHaveBeenCalled();
  });
});
