import { describe, expect, it, vi } from "vitest";
import { START } from "@langchain/langgraph/web";
import type { Client, ThreadState } from "@langchain/langgraph-sdk";
import type { GraphConfig, GraphState } from "@openswe/shared/open-swe/types";
import { createNewSession } from "./route";

describe("createNewSession", () => {
  it("uses START entry point when thread state has no next nodes", async () => {
    const runsCreate = vi.fn().mockResolvedValue({ run_id: "run-123" });
    const client = {
      runs: {
        create: runsCreate,
      },
    } as unknown as Client;

    const threadState = {
      values: {} as GraphState,
      next: [],
      checkpoint: {} as any,
      metadata: {} as any,
      created_at: null,
      parent_checkpoint: null,
      tasks: [],
    } as ThreadState<GraphState>;

    const threadConfig = {
      configurable: undefined,
    } as unknown as GraphConfig;

    await createNewSession(client, {
      graphId: "test-graph",
      threadState,
      threadConfig,
    });

    expect(runsCreate).toHaveBeenCalledWith(
      expect.any(String),
      "test-graph",
      expect.objectContaining({
        command: expect.objectContaining({ goto: START }),
      }),
    );
  });
});
