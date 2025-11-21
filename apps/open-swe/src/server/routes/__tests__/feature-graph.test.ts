import { jest } from "@jest/globals";
import { Hono } from "hono";
import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import { registerFeatureGraphRoute } from "../feature-graph.js";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import type { PlannerGraphUpdate } from "@openswe/shared/open-swe/planner/types";
import type { TaskPlan } from "@openswe/shared/open-swe/types";
import { createLangGraphClient } from "../../../utils/langgraph-client.js";

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

    const managerState: { values: ManagerGraphState } = {
      values: {
        targetRepository: { owner: "acme", repo: "demo" },
        taskPlan: { tasks: [], activeTaskIndex: 0 } satisfies TaskPlan,
        branchName: "main",
        messages: [],
        featureGraph,
        activeFeatureIds: [],
        plannerSession: {
          threadId: "planner-thread",
          runId: "existing-run",
        },
      },
    };

    const runsCreate = jest
      .fn<
        (
          threadId: string,
          graphId: string,
          options: { input: PlannerGraphUpdate },
        ) => Promise<{ run_id: string }>
      >()
      .mockResolvedValue({ run_id: "new-run" });
    const updateState = jest
      .fn<
        (
          threadId: string,
          update: { values: ManagerGraphState; asNode: string },
        ) => Promise<void>
      >()
      .mockResolvedValue(undefined);
    const getState = jest
      .fn<
        (threadId: string) => Promise<{
          values: ManagerGraphState;
          metadata?: Record<string, unknown>;
        }>
      >()
      .mockResolvedValue(managerState);

    const clientFactory = jest.fn<typeof createLangGraphClient>(
      () =>
        ({
          runs: { create: runsCreate },
          threads: { getState, updateState },
        } as unknown as ReturnType<typeof createLangGraphClient>),
    );

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
    expect(getState).toHaveBeenCalledWith("manager-thread");
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

    expect(updateState).toHaveBeenCalledWith("manager-thread", {
      values: expect.objectContaining({
        plannerSession: { threadId: "planner-thread", runId: "new-run" },
        activeFeatureIds: ["feature-primary"],
      }),
      asNode: "start-planner",
    });
  });
});
