/**
 * Utility functions for CLI app
 */

import { Client, StreamMode } from "@langchain/langgraph-sdk";
import { encryptSecret } from "@open-swe/shared/crypto";
import {
  OPEN_SWE_STREAM_MODE,
  PLANNER_GRAPH_ID,
  LOCAL_MODE_HEADER,
} from "@open-swe/shared/constants";
import {
  getAccessToken,
  getInstallationAccessToken,
  getInstallationId,
} from "./auth-server.js";
import { formatDisplayLog } from "./logger.js";
const LANGGRAPH_URL = process.env.LANGGRAPH_URL || "http://localhost:2024";

/**
 * Submit feedback to the planner
 */
export async function submitFeedback({
  plannerFeedback,
  plannerThreadId,
  selectedRepo,
  setLogs,
  setPlannerFeedback,
  setStreamingPhase,
}: {
  plannerFeedback: string;
  plannerThreadId: string;
  selectedRepo: any;
  // eslint-disable-next-line no-unused-vars
  setLogs: (updater: (prev: string[]) => string[]) => void;
  setPlannerFeedback: () => void;
  // eslint-disable-next-line no-unused-vars
  setStreamingPhase: (phase: "streaming" | "awaitingFeedback" | "done") => void;
}) {
  try {
    // Set streaming phase back to streaming when feedback submission starts
    setStreamingPhase("streaming");

    const isLocalMode = process.env.OPEN_SWE_LOCAL_MODE === "true";
    let client: Client;

    if (isLocalMode) {
      // In local mode, create client without GitHub authentication
      client = new Client({
        apiUrl: LANGGRAPH_URL,
        defaultHeaders: {
          [LOCAL_MODE_HEADER]: "true", // Signal to server this is local mode
        },
      });
    } else {
      const userAccessToken = getAccessToken();
      const installationAccessToken = await getInstallationAccessToken();
      const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;

      if (!userAccessToken || !installationAccessToken || !encryptionKey) {
        setLogs((prev) => [
          ...prev,
          "Missing access tokens for feedback submission",
        ]);
        return;
      }

      const encryptedUserToken = encryptSecret(userAccessToken, encryptionKey);
      const encryptedInstallationToken = encryptSecret(
        installationAccessToken,
        encryptionKey,
      );
      const [owner] = selectedRepo?.full_name.split("/") || [];

      const installationId = getInstallationId();
      client = new Client({
        apiUrl: LANGGRAPH_URL,
        defaultHeaders: {
          "x-github-access-token": encryptedUserToken,
          "x-github-installation-token": encryptedInstallationToken,
          "x-github-installation-name": owner,
          "x-github-installation-id": installationId,
        },
      });
    }

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
