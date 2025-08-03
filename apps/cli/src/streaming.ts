import { Client, StreamMode } from "@langchain/langgraph-sdk";
import { v4 as uuidv4 } from "uuid";
import {
  MANAGER_GRAPH_ID,
  LOCAL_MODE_HEADER,
  OPEN_SWE_STREAM_MODE,
} from "@open-swe/shared/constants";
import { formatDisplayLog } from "./logger.js";
import { isAgentInboxInterruptSchema } from "@open-swe/shared/agent-inbox-interrupt";

const LANGGRAPH_URL = process.env.LANGGRAPH_URL || "http://localhost:2024";

interface StreamingCallbacks {
  setLogs: (updater: (prev: string[]) => string[]) => void; // eslint-disable-line no-unused-vars
  setPlannerThreadId: (id: string) => void; // eslint-disable-line no-unused-vars
  setStreamingPhase: (phase: "streaming" | "awaitingFeedback" | "done") => void; // eslint-disable-line no-unused-vars
  setLoadingLogs: (loading: boolean) => void; // eslint-disable-line no-unused-vars
}

export class StreamingService {
  private callbacks: StreamingCallbacks;

  constructor(callbacks: StreamingCallbacks) {
    this.callbacks = callbacks;
  }

  private async handleProgrammerStream(
    client: Client,
    programmerThreadId: string,
    programmerRunId: string,
  ) {
    for await (const programmerChunk of client.runs.joinStream(
      programmerThreadId,
      programmerRunId,
      {
        streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
      },
    )) {
      if (programmerChunk.event === "updates") {
        const formatted = formatDisplayLog(programmerChunk);
        if (formatted.length > 0) {
          this.callbacks.setLogs((prev) => [...prev, ...formatted]);
        }
      }
    }
  }

  private async handlePlannerStream(
    client: Client,
    plannerThreadId: string,
    plannerRunId: string,
  ): Promise<{ needsFeedback: boolean }> {
    let programmerStreamed = false;

    for await (const subChunk of client.runs.joinStream(
      plannerThreadId,
      plannerRunId,
      {
        streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
      },
    )) {
      if (subChunk.event === "updates") {
        const formatted = formatDisplayLog(subChunk);
        // Filter out human messages from planner stream (already logged in manager)
        const filteredFormatted = formatted.filter(
          (log) => !log.startsWith("[HUMAN]"),
        );
        if (filteredFormatted.length > 0) {
          this.callbacks.setLogs((prev) => [...prev, ...filteredFormatted]);
        }
      }

      // Check for programmer session
      if (
        !programmerStreamed &&
        subChunk.data?.programmerSession?.threadId &&
        typeof subChunk.data.programmerSession.threadId === "string" &&
        typeof subChunk.data.programmerSession.runId === "string"
      ) {
        programmerStreamed = true;
        await this.handleProgrammerStream(
          client,
          subChunk.data.programmerSession.threadId,
          subChunk.data.programmerSession.runId,
        );
      }

      // Detect HumanInterrupt in planner stream
      const interruptArr =
        subChunk.data && Array.isArray(subChunk.data["__interrupt__"])
          ? subChunk.data["__interrupt__"]
          : undefined;
      const firstInterruptValue =
        interruptArr && interruptArr[0] && interruptArr[0].value
          ? interruptArr[0].value
          : undefined;

      if (isAgentInboxInterruptSchema(firstInterruptValue)) {
        return { needsFeedback: true };
      }
    }

    return { needsFeedback: false };
  }

  private async startManagerStream(
    client: Client,
    threadId: string,
    runId: string,
  ) {
    let plannerStreamed = false;

    for await (const chunk of client.runs.joinStream(threadId, runId)) {
      if (chunk.event === "updates") {
        const formatted = formatDisplayLog(chunk);
        if (formatted.length > 0) {
          this.callbacks.setLogs((prev) => {
            if (prev.length === 0) {
              this.callbacks.setLoadingLogs(false);
            }
            return [...prev, ...formatted];
          });
        }
      }

      // Check for plannerSession
      if (
        !plannerStreamed &&
        chunk.data &&
        chunk.data.plannerSession &&
        typeof chunk.data.plannerSession.threadId === "string" &&
        typeof chunk.data.plannerSession.runId === "string"
      ) {
        plannerStreamed = true;
        this.callbacks.setPlannerThreadId(chunk.data.plannerSession.threadId);

        const result = await this.handlePlannerStream(
          client,
          chunk.data.plannerSession.threadId,
          chunk.data.plannerSession.runId,
        );

        if (result.needsFeedback) {
          this.callbacks.setStreamingPhase("awaitingFeedback");
          return; // Pause streaming, let React render feedback prompt
        }
      }
    }

    this.callbacks.setStreamingPhase("done");
  }

  async startNewSession(prompt: string) {
    this.callbacks.setLogs(() => []);
    this.callbacks.setLoadingLogs(true);

    try {
      const runInput = {
        messages: [
          {
            id: uuidv4(),
            type: "human",
            content: [{ type: "text", text: prompt }],
          },
        ],
        targetRepository: {
          owner: "local",
          repo: "local",
          branch: "main",
        },
        autoAcceptPlan: false,
      };

      const headers = {
        [LOCAL_MODE_HEADER]: "true",
      };

      const newClient = new Client({
        apiUrl: LANGGRAPH_URL,
        defaultHeaders: headers,
      });

      const thread = await newClient.threads.create();
      const threadId = thread.thread_id;

      const run = await newClient.runs.create(threadId, MANAGER_GRAPH_ID, {
        input: runInput,
        config: {
          recursion_limit: 400,
        },
        ifNotExists: "create",
        streamResumable: true,
        streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
      });

      await this.startManagerStream(newClient, threadId, run.run_id);
    } catch (err: any) {
      this.callbacks.setLogs((prev) => [
        ...prev,
        `Error during streaming: ${err.message}`,
      ]);
      this.callbacks.setLoadingLogs(false);
    } finally {
      this.callbacks.setLoadingLogs(false);
    }
  }
}
