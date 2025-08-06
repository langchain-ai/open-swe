/**
 * Utility functions for CLI app
 */

import { Client, StreamMode } from "@langchain/langgraph-sdk";
import {
  OPEN_SWE_STREAM_MODE,
  PLANNER_GRAPH_ID,
  LOCAL_MODE_HEADER,
} from "@open-swe/shared/constants";
import { formatDisplayLog } from "./logger.js";

const LANGGRAPH_URL = process.env.LANGGRAPH_URL || "http://localhost:2024";

/**
 * Submit feedback to the planner
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
    const stream = await client.runs.stream(plannerThreadId, PLANNER_GRAPH_ID, {
      command: {
        resume: [
          {
            type: plannerFeedback === "approve" ? "accept" : "ignore",
            args: null,
          },
        ],
      },
      streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
    });

    let programmerStreamed = false;
    // Process the stream response
    for await (const chunk of stream) {
      const formatted = formatDisplayLog(chunk);
      if (formatted.length > 0) {
        setLogs((prev) => [...prev, ...formatted]);
      }

      // Check for programmer session in the resumed planner stream
      const chunkData = chunk.data as any;
      if (
        !programmerStreamed &&
        chunkData?.programmerSession?.threadId &&
        typeof chunkData.programmerSession.threadId === "string" &&
        typeof chunkData.programmerSession.runId === "string"
      ) {
        programmerStreamed = true;
        // Join programmer stream
        for await (const programmerChunk of client.runs.joinStream(
          chunkData.programmerSession.threadId,
          chunkData.programmerSession.runId,
        )) {
          const formatted = formatDisplayLog(programmerChunk);
          if (formatted.length > 0) {
            setLogs((prev) => [...prev, ...formatted]);
          }
        }
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
