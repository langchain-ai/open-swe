import { formatDisplayLog } from "./logger.js";

export interface TraceReplayCallbacks {
  setLogs: (updater: (prev: string[]) => string[]) => void; // eslint-disable-line no-unused-vars
  setLoadingLogs: (loading: boolean) => void; // eslint-disable-line no-unused-vars
}

export class TraceReplayService {
  private callbacks: TraceReplayCallbacks;
  private rawLogs: any[] = [];

  constructor(callbacks: TraceReplayCallbacks) {
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

  async replayFromTrace(langsmithRun: any, playbackSpeed: number = 500) {
    this.rawLogs = [];
    this.callbacks.setLogs(() => []);
    this.callbacks.setLoadingLogs(true);

    try {
      const messages = langsmithRun.messages || [];

      for (let i = 0; i < messages.length; i++) {
        const message = messages[i];

        // Convert LangSmith message to the format expected by formatDisplayLog
        const mockChunk = {
          event: "updates",
          data: {
            agent: {
              messages: [message],
            },
          },
        };
        this.rawLogs.push(mockChunk);
        this.updateDisplay();

        if (this.rawLogs.length === 1) {
          this.callbacks.setLoadingLogs(false);
        }

        // Add delay between messages to simulate streaming
        if (i < messages.length - 1) {
          await new Promise((resolve) => setTimeout(resolve, playbackSpeed));
        }
      }

      // Check for interrupt data in the trace and add it at the end
      if (langsmithRun.__interrupt__ || langsmithRun.interrupt) {
        const interruptData =
          langsmithRun.__interrupt__ || langsmithRun.interrupt;
        const interruptChunk = {
          event: "interrupt",
          data: {
            __interrupt__: Array.isArray(interruptData)
              ? interruptData
              : [interruptData],
          },
        };
        this.rawLogs.push(interruptChunk);
        this.updateDisplay();
      }
    } catch (err: any) {
      this.rawLogs.push(`Error during replay: ${err.message}`);
      this.updateDisplay();
      this.callbacks.setLoadingLogs(false);
    } finally {
      this.callbacks.setLoadingLogs(false);
    }
  }
}
