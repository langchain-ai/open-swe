import { randomUUID } from "node:crypto";

/** What the person chose for a local action. */
type Decision = "deny" | "once" | "grant";

/** What a pending local action asks of the person, as the approval panel shows it. */
interface ApprovalRequest {
  kind: "read" | "write" | "import" | "execute";
  threadId: string | null;
  threadTitle: string | null;
  /** Why the runtime's reviewer wants the person to look, when it does. */
  concern: string | null;
  /** The exact action: path and contents, or working folder and command. */
  detail: string;
  /** The folder a thread grant would cover, as the person writes it, if one is offered. */
  grantFolder: string | null;
}

interface PendingApproval extends ApprovalRequest {
  id: string;
  createdAt: number;
}

/**
 * Local actions waiting on the person. Only the desktop's own approval panel decides
 * them, by id; the dashboard page can list a thread's requests and ask to review one,
 * never answer one.
 */
class ApprovalQueue {
  private readonly pending = new Map<
    string,
    { request: PendingApproval; settle: (decision: Decision) => void }
  >();

  constructor(private readonly changed: () => void = () => {}) {}

  /** Waits for the person's decision; an aborted request is denied and dropped. */
  request(request: ApprovalRequest, signal: AbortSignal): Promise<Decision> {
    if (signal.aborted) return Promise.resolve("deny");
    const id = randomUUID();
    return new Promise<Decision>((resolve) => {
      const settle = (decision: Decision) => {
        if (!this.pending.delete(id)) return;
        signal.removeEventListener("abort", abort);
        resolve(decision);
        this.changed();
      };
      const abort = () => settle("deny");
      signal.addEventListener("abort", abort, { once: true });
      this.pending.set(id, {
        request: { ...request, id, createdAt: Date.now() },
        settle,
      });
      this.changed();
    });
  }

  decide(id: string, decision: Decision): boolean {
    const entry = this.pending.get(id);
    if (!entry) return false;
    entry.settle(decision);
    return true;
  }

  get(id: string): PendingApproval | undefined {
    return this.pending.get(id)?.request;
  }

  /** The oldest request, which the panel shows first. */
  first(): PendingApproval | undefined {
    return this.pending.values().next().value?.request;
  }

  list(threadId?: string): PendingApproval[] {
    return [...this.pending.values()]
      .map((entry) => entry.request)
      .filter((request) => !threadId || request.threadId === threadId);
  }

  pendingThreads(): string[] {
    return [
      ...new Set(
        this.list().flatMap(({ threadId }) => (threadId ? [threadId] : [])),
      ),
    ];
  }

  /** Denies every request, as when local access closes. */
  clear(): void {
    // oxlint-disable-next-line unicorn/no-useless-spread -- settling removes each entry
    for (const { settle } of [...this.pending.values()]) settle("deny");
  }
}

module.exports = { ApprovalQueue };
