import { describe, expect, it } from "@jest/globals";
import { TaskPlan } from "@openswe/shared/open-swe/types";
import { createClassificationPromptAndToolSchema } from "../graphs/manager/nodes/classify-message/utils.js";

const baseTaskPlan = {
  tasks: [
    {
      id: "task-1",
      taskIndex: 0,
      request: "stub request",
      title: "stub title",
      createdAt: Date.now(),
      updatedAt: Date.now(),
      completed: false,
      planRevisions: [
        {
          revisionIndex: 0,
          plans: [
            {
              index: 0,
              plan: "placeholder step",
              completed: false,
            },
          ],
          createdAt: Date.now(),
          createdBy: "agent",
        },
      ],
      activeRevisionIndex: 0,
    },
  ],
  activeTaskIndex: 0,
} as TaskPlan;

const buildPrompt = () =>
  createClassificationPromptAndToolSchema({
    programmerStatus: "not_started",
    plannerStatus: "not_started",
    messages: [],
    taskPlan: baseTaskPlan,
    requestSource: "open-swe",
  }).prompt;

describe("classify-message prompt", () => {
  it("emphasizes collaborative, incremental feature graph discovery", () => {
    const prompt = buildPrompt();

    expect(prompt).toContain("Start by asking 1–3 targeted, code-aware questions");
    expect(prompt).toContain("Never dump a full plan or multi-step solution");
    expect(prompt).toContain("only propose the smallest next increment after the user responds");
    expect(prompt).toContain("Monologue proposals are discouraged");
    expect(prompt).toContain("Expected traffic/size for the signals?");
    expect(prompt).toContain("Hardware budget or GPUs available?");
    expect(prompt).toContain("Where does the data live and how often is it refreshed?");
  });

  it("keeps feature graph routing responses concise", () => {
    const prompt = buildPrompt();

    expect(prompt).toContain("- feature_graph_orchestrator:");
    expect(prompt).toContain("Your response will not exceed two sentences.");
    expect(prompt).toContain("keep responses under two sentences until the user answers your questions");
  });
});
