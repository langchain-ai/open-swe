/**
 * Utility functions for CLI app
 */

import { Client, StreamMode } from "@langchain/langgraph-sdk";
import {
  OPEN_SWE_STREAM_MODE,
  LOCAL_MODE_HEADER,
  OPEN_SWE_V2_GRAPH_ID,
} from "@open-swe/shared/constants";
import { formatDisplayLog } from "./logger.js";

const LANGGRAPH_URL = process.env.LANGGRAPH_URL || "http://localhost:2024";

/**
 * Submit feedback to the coding agent
 */
export async function submitFeedback({
  plannerFeedback,
  plannerThreadId,
  setLogs,
  setPlannerFeedback,
  setStreamingPhase,
}: {
  plannerFeedback: string;
  plannerThreadId: string;
  setLogs: (updater: (prev: string[]) => string[]) => void; // eslint-disable-line no-unused-vars
  setPlannerFeedback: () => void;
  setStreamingPhase: (phase: "streaming" | "awaitingFeedback" | "done") => void; // eslint-disable-line no-unused-vars
}) {
  try {
    // Set streaming phase back to streaming when feedback submission starts
    setStreamingPhase("streaming");

    // Create client for local mode
    const client = new Client({
      apiUrl: LANGGRAPH_URL,
      defaultHeaders: {
        [LOCAL_MODE_HEADER]: "true",
      },
    });

    const formatted = formatDisplayLog(`Human feedback: ${plannerFeedback}`);
    if (formatted.length > 0) {
      setLogs((prev) => [...prev, ...formatted]);
    }

    // Create a new stream with the feedback
    const stream = await client.runs.stream(
      plannerThreadId,
      OPEN_SWE_V2_GRAPH_ID,
      {
        command: {
          resume: [
            {
              type: plannerFeedback === "approve" ? "accept" : "ignore",
              args: null,
            },
          ],
        },
        streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
      },
    );

    // Process the stream response
    for await (const chunk of stream) {
      const formatted = formatDisplayLog(chunk);
      if (formatted.length > 0) {
        setLogs((prev) => [...prev, ...formatted]);
      }
    }

    // Set streaming phase to done when complete
    setStreamingPhase("done");
  } catch (error: unknown) {
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error";
    setLogs((prev) => [...prev, `Error submitting feedback: ${errorMessage}`]);
    // Set streaming phase to done even on error
    setStreamingPhase("done");
  } finally {
    // Clear feedback state
    setPlannerFeedback();
  }
}
