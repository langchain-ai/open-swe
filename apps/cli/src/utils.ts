/**
 * Utility functions for CLI app
 */

import { Client, StreamMode } from "@langchain/langgraph-sdk";
import { encryptSecret } from "@open-swe/shared/crypto";
import {
  OPEN_SWE_STREAM_MODE,
  PLANNER_GRAPH_ID,
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
}: {
  plannerFeedback: string;
  plannerThreadId: string;
  selectedRepo: any;
  setLogs: (updater: (prev: string[]) => string[]) => void; // eslint-disable-line no-unused-vars
  setPlannerFeedback: () => void;
}) {
  try {
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
    const client = new Client({
      apiUrl: LANGGRAPH_URL,
      defaultHeaders: {
        "x-github-access-token": encryptedUserToken,
        "x-github-installation-token": encryptedInstallationToken,
        "x-github-installation-name": owner,
        "x-github-installation-id": installationId,
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
  } catch (error: unknown) {
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error";
    setLogs((prev) => [...prev, `Error submitting feedback: ${errorMessage}`]);
  } finally {
    // Clear feedback state
    setPlannerFeedback();
  }
}
