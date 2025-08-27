import { Client, StreamMode } from "@langchain/langgraph-sdk";
import { LOCAL_MODE_HEADER } from "@open-swe/shared/constants";
import { formatDisplayLog } from "./logger.js";

const LANGGRAPH_URL = process.env.LANGGRAPH_URL || "http://localhost:2024";

interface InterruptData {
  command: string;
  args: Record<string, string | number | boolean>;
  id: string;
}

interface InterruptItem {
  id: string;
  value: InterruptData;
}

interface StreamChunk {
  event: string;
  data: ChunkData;
}

interface ChunkData {
  __interrupt__?: InterruptItem[];
  agent?: {
    messages: Array<{
      role: string;
      content: string;
    }>;
  };
  [key: string]: unknown;
}

interface StreamingCallbacks {
  setLogs: (updater: (prev: string[]) => string[]) => void; // eslint-disable-line no-unused-vars
  setLoadingLogs: (loading: boolean) => void; // eslint-disable-line no-unused-vars
  setCurrentInterrupt: (interrupt: InterruptData | null) => void; // eslint-disable-line no-unused-vars
  setStreamingPhase: (phase: string) => void; // eslint-disable-line no-unused-vars
}

export class StreamingService {
  private callbacks: StreamingCallbacks;
  private client: Client | null = null;
  private threadId: string | null = null;
  private rawLogs: (string | StreamChunk)[] = [];

  constructor(callbacks: StreamingCallbacks) {
    this.callbacks = callbacks;
  }

  /**
   * Get formatted logs for display
   */
  getFormattedLogs(): string[] {
    const formattedLogs: string[] = [];

    for (const chunk of this.rawLogs) {
      if (typeof chunk === "string") {
        const formatted = formatDisplayLog(chunk);
        formattedLogs.push(...formatted);
      } else if (chunk && chunk.data) {
        // Process all chunks with data, not just "updates" events
        const formatted = formatDisplayLog(chunk);
        formattedLogs.push(...formatted);
      }
    }

    return formattedLogs;
  }

  /**
   * Update the display with formatted logs
   */
  private updateDisplay() {
    const formattedLogs = this.getFormattedLogs();
    this.callbacks.setLogs(() => formattedLogs);
  }

  /**
   * Start a new session
   */
  async startNewSession(prompt: string) {
    this.rawLogs = [];
    this.callbacks.setLogs(() => []);
    this.callbacks.setLoadingLogs(true);

    // Keeping for the future, not needed now
    try {
      const headers = {
        [LOCAL_MODE_HEADER]: "true",
      };

      this.client = new Client({
        apiUrl: LANGGRAPH_URL,
        defaultHeaders: headers,
      });

      const thread = await this.client.threads.create();
      this.threadId = thread.thread_id;

      // Stream using the pattern from deep-agents
      const stream = await this.client.runs.stream(this.threadId, "coding", {
        input: {
          messages: [
            {
              role: "user",
              content: prompt,
            },
          ],
          working_directory:
            process.env.OPEN_SWE_LOCAL_PROJECT_PATH || process.cwd(),
        },
        streamMode: ["updates"] as StreamMode[],
        config: { recursion_limit: 1000 },
      });

      // Process the stream
      for await (const chunk of stream) {
        this.updateDisplay();

        if (chunk.event === "updates") {
          // Check for interrupts in the chunk
          if (chunk.data && chunk.data.__interrupt__) {
            const chunkData = chunk.data as ChunkData;
            const interrupt = chunkData.__interrupt__?.[0]?.value;
            if (interrupt?.command && interrupt?.args) {
              this.callbacks.setCurrentInterrupt({
                command: interrupt.command,
                args: interrupt.args,
                id: chunkData.__interrupt__?.[0]?.id || "unknown",
              });
            }
          }

          // Store raw chunk instead of formatting immediately
          this.rawLogs.push(chunk);
          this.updateDisplay();

          if (this.rawLogs.length === 1) {
            this.callbacks.setLoadingLogs(false);
          }
        }
      }

      this.callbacks.setStreamingPhase("done");
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : "Unknown error";
      this.rawLogs.push(`Error during streaming: ${errorMessage}`);
      this.updateDisplay();
      this.callbacks.setLoadingLogs(false);
    } finally {
      this.callbacks.setLoadingLogs(false);
    }
  }

  async submitInterruptResponse(response: boolean | string) {
    if (!this.client || !this.threadId) {
      throw new Error("No active stream session. Start a new session first.");
    }

    // Clear the interrupt from UI
    this.callbacks.setCurrentInterrupt(null);
    this.callbacks.setLoadingLogs(true);

    try {
      const stream = await this.client.runs.stream(this.threadId, "coding", {
        command: { resume: response },
        streamMode: ["updates"] as StreamMode[],
        config: { recursion_limit: 1000 },
      });

      // Process the stream
      for await (const chunk of stream) {
        if (chunk.event === "updates") {
          // Check for interrupts in the chunk
          if (chunk.data && chunk.data.__interrupt__) {
            const chunkData = chunk.data as ChunkData;
            const interrupt = chunkData.__interrupt__?.[0]?.value;
            if (interrupt?.command && interrupt?.args) {
              this.callbacks.setCurrentInterrupt({
                command: interrupt.command,
                args: interrupt.args,
                id: chunkData.__interrupt__?.[0]?.id || "unknown",
              });
            }
          }

          // Store raw chunk instead of formatting immediately
          this.rawLogs.push(chunk);
          this.updateDisplay();

          if (this.rawLogs.length === 1) {
            this.callbacks.setLoadingLogs(false);
          }
        }
      }

      this.callbacks.setStreamingPhase("done");
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : "Unknown error";
      this.rawLogs.push(`Error submitting approval: ${errorMessage}`);
      this.updateDisplay();
      this.callbacks.setLoadingLogs(false);
    } finally {
      this.callbacks.setLoadingLogs(false);
    }
  }

  async submitToExistingStream(prompt: string) {
    if (!this.client || !this.threadId) {
      throw new Error("No active stream session. Start a new session first.");
    }

    // Don't clear logs - continue the conversation
    this.callbacks.setLoadingLogs(true);

    try {
      const stream = await this.client.runs.stream(this.threadId, "coding", {
        input: {
          messages: [{ role: "user", content: prompt }],
        },
        streamMode: ["updates"] as StreamMode[],
        config: { recursion_limit: 1000 },
      });

      // Process the stream
      for await (const chunk of stream) {
        if (chunk.event === "updates") {
          // Check for interrupts in the chunk
          if (chunk.data && chunk.data.__interrupt__) {
            const chunkData = chunk.data as ChunkData;
            const interrupt = chunkData.__interrupt__?.[0]?.value;
            if (interrupt?.command && interrupt?.args) {
              this.callbacks.setCurrentInterrupt({
                command: interrupt.command,
                args: interrupt.args,
                id: chunkData.__interrupt__?.[0]?.id || "unknown",
              });
            }
          }

          // Store raw chunk instead of formatting immediately
          this.rawLogs.push(chunk);
          this.updateDisplay();

          if (this.rawLogs.length === 1) {
            this.callbacks.setLoadingLogs(false);
          }
        }
      }
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : "Unknown error";
      this.rawLogs.push(`Error submitting to stream: ${errorMessage}`);
      this.updateDisplay();
      this.callbacks.setLoadingLogs(false);
    } finally {
      this.callbacks.setLoadingLogs(false);
    }
  }
}
