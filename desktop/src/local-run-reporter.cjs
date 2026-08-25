function uiMessages(state) {
  const messages = state?.values?.messages;
  if (!Array.isArray(messages)) return [];
  return messages.map((message, index) => {
    const kind = message?.type || message?.role;
    const author = kind === "human" || kind === "user" ? "user" : kind === "ai" || kind === "assistant" ? "agent" : kind === "tool" ? "tool" : "system";
    const content = message?.content;
    const chunks = typeof content === "string"
      ? [{ kind: "text", text: content }]
      : Array.isArray(content)
        ? content.filter((item) => item?.type === "text" && typeof item.text === "string").map((item) => ({ kind: "text", text: item.text }))
        : [];
    return {
      id: String(message?.id || `${author}-${index}`),
      author,
      timestamp: new Date().toISOString(),
      chunks,
    };
  });
}

class LocalRunReporter {
  constructor({ supervisor, device, report, intervalMs = 1000, heartbeatIntervalMs = 30_000 }) {
    this.supervisor = supervisor;
    this.device = device;
    this.report = report;
    this.intervalMs = intervalMs;
    this.heartbeatIntervalMs = heartbeatIntervalMs;
    this.lastHeartbeat = 0;
    this.runs = new Map();
    this.timer = null;
  }

  track(threadId, runId) {
    this.runs.set(threadId, { runId, status: "queued" });
    void this.send(threadId, runId, "queued");
    this.start();
  }

  heartbeat(threadId) {
    this.lastHeartbeat = Date.now();
    void this.send(threadId, null, null);
    this.start();
  }

  start() {
    if (this.timer) return;
    this.timer = setInterval(() => void this.poll(), this.intervalMs);
    this.timer.unref?.();
  }

  async send(threadId, runId, status, extra = {}) {
    await this.report({
      ...(threadId ? { thread_id: threadId } : {}),
      device_id: this.device.id,
      device_name: this.device.name,
      ...(runId ? { run_id: runId } : {}),
      ...(status ? { status } : {}),
      ...extra,
    });
  }

  async poll() {
    if (Date.now() - this.lastHeartbeat >= this.heartbeatIntervalMs) {
      this.lastHeartbeat = Date.now();
      await this.send(null, null, null).catch(() => undefined);
    }
    if (this.runs.size === 0) return;
    const activity = await this.supervisor.threadActivity();
    if (!activity) return;
    for (const [threadId, tracked] of this.runs) {
      if (activity[threadId] === "running") {
        if (tracked.status !== "running") {
          tracked.status = "running";
          await this.send(threadId, tracked.runId, "running");
        }
        continue;
      }
      const run = await this.supervisor.getRun(threadId, tracked.runId);
      const status = run?.status === "success" ? "finished" : run?.status === "interrupted" ? "interrupted" : "error";
      const state = await this.supervisor.getThreadState(threadId);
      await this.send(threadId, tracked.runId, status, {
        ...(status === "error" ? { error: String(run?.error || run?.status || "local run failed") } : {}),
        messages: uiMessages(state),
      });
      this.runs.delete(threadId);
    }
  }

  close() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}

module.exports = { LocalRunReporter, uiMessages };
