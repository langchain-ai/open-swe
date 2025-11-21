import { jest } from "@jest/globals";
import { Hono } from "hono";
import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import { registerFeatureGraphRoute } from "../feature-graph.js";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import type { TaskPlan } from "@openswe/shared/open-swe/types";

describe("feature graph develop route", () => {
  it("starts a planner run with feature context even when a session already exists", async () => {
    const featureGraph = new FeatureGraph({
      version: 1,
      nodes: new Map([
        [
          "feature-primary",
          {
            id: "feature-primary",
            name: "Primary feature",
            description: "Implements the primary capability",
            status: "active",
          },
        ],
        [
          "feature-supporting",
          {
            id: "feature-supporting",
            name: "Supporting feature",
            description: "Enables the primary capability",
            status: "planned",
          },
        ],
      ]),
      edges: [
        { source: "feature-supporting", target: "feature-primary", type: "upstream" },
      ],
    });

    const managerState = {
      values: {
        targetRepository: { owner: "acme", repo: "demo" },
        taskPlan: { plans: [] } as unknown as TaskPlan,
        branchName: "main",
        messages: [],
        internalMessages: [],
        featureGraph,
        activeFeatureIds: [],
        plannerSession: {
          threadId: "planner-thread",
          runId: "existing-run",
        },
      },
    } as unknown as { values: ManagerGraphState };

    const runsCreate = jest.fn().mockResolvedValue({ run_id: "new-run" });
    const updateState = jest.fn().mockResolvedValue(undefined);
    const getState = jest.fn().mockResolvedValue(managerState);

    const clientFactory = jest.fn().mockReturnValue({
      runs: { create: runsCreate },
      threads: { getState, updateState },
    } as any);

    const app = new Hono();
    registerFeatureGraphRoute(app, { clientFactory });

    const response = await app.request("/feature-graph/develop", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ thread_id: "manager-thread", feature_id: "feature-primary" }),
    });

    expect(response.status).toBe(200);
    const payload = (await response.json()) as { planner_thread_id: string; run_id: string };

    expect(payload).toEqual({ planner_thread_id: "planner-thread", run_id: "new-run" });
    expect(getState).toHaveBeenCalledWith<ManagerGraphState>("manager-thread");
    expect(runsCreate).toHaveBeenCalledTimes(1);

    const [runThreadId, , runOptions] = runsCreate.mock.calls[0];
    expect(runThreadId).toBe("planner-thread");
    expect(runOptions.input.activeFeatureIds).toEqual(["feature-primary"]);
    expect(runOptions.input.features?.map((feature: { id: string }) => feature.id)).toEqual([
      "feature-primary",
      "feature-supporting",
    ]);
    expect(
      runOptions.input.featureDependencies?.map((feature: { id: string }) => feature.id),
    ).toEqual(["feature-supporting"]);

    expect(updateState).toHaveBeenCalledWith<ManagerGraphState>("manager-thread", {
      values: expect.objectContaining({
        plannerSession: { threadId: "planner-thread", runId: "new-run" },
        activeFeatureIds: ["feature-primary"],
      }),
      asNode: "start-planner",
    });
  });
});
