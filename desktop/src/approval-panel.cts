import path from "node:path";
import { type View, type WebContents, WebContentsView } from "electron";

type Decision = "deny" | "once" | "grant";

interface PendingApproval {
  id: string;
  threadId: string | null;
  kind: string;
  threadTitle: string | null;
  concern: string | null;
  detail: string;
  grantFolder: string | null;
}

interface Queue {
  list(): PendingApproval[];
  get(id: string): PendingApproval | undefined;
  decide(id: string, decision: Decision): boolean;
}

const DECIDE_URL = "open-swe-approval://decide";

interface PanelHost {
  webContents: Pick<WebContents, "getURL" | "getZoomFactor">;
  contentView: {
    addChildView(view: View): void;
    removeChildView(view: View): void;
  };
  isDestroyed(): boolean;
  getContentBounds(): { width: number; height: number };
  once(event: "closed", listener: () => void): unknown;
}

/**
 * The desktop's own approval surface, laid over the app window: a page bundled with
 * the app, not the dashboard the backend serves, so nothing the backend or the page
 * sends can answer it. Only a person choosing in it decides a local action.
 */
class ApprovalPanel {
  private view: WebContentsView | null = null;
  private window: PanelHost | null = null;
  private showing: string | null = null;
  private scoped = false;
  private scope: {
    threadId: string;
    bounds: { x: number; y: number; width: number; height: number };
  } | null = null;
  /** Requests the person put off; they stay pending until decided or expired. */
  private readonly later = new Set<string>();
  /** A request the person asked to review from its thread, shown before the others. */
  private chosen: string | null = null;

  constructor(
    private readonly queue: Queue,
    private readonly currentWindow: () => PanelHost | null,
    private readonly displayFolder: (folder: string) => string,
  ) {}

  setThread(threadId: unknown, bounds: unknown): boolean {
    if (typeof threadId !== "string" || !threadId || threadId.length > 256)
      return false;
    this.scoped = true;
    if (bounds === null) {
      if (this.scope?.threadId === threadId) this.scope = null;
    } else {
      if (
        typeof bounds !== "object" ||
        !bounds ||
        !("x" in bounds) ||
        !("y" in bounds) ||
        !("width" in bounds) ||
        !("height" in bounds)
      ) {
        this.setThread(threadId, null);
        return false;
      }
      const { x, y, width, height } = bounds;
      if (
        typeof x !== "number" ||
        typeof y !== "number" ||
        typeof width !== "number" ||
        typeof height !== "number" ||
        ![x, y, width, height].every(Number.isFinite) ||
        x < 0 ||
        y < 0 ||
        width <= 0 ||
        height <= 0
      ) {
        this.setThread(threadId, null);
        return false;
      }
      this.scope = { threadId, bounds: { x, y, width, height } };
    }
    this.sync();
    return true;
  }

  /** Opens the panel on `id`, as a thread's Review button asks. */
  review(id: string): void {
    const request = this.queue.get(id);
    if (!request || !this.matches(request)) return;
    this.later.delete(id);
    this.chosen = id;
    this.sync();
  }

  /** Shows the request to decide now, or hides the panel when none is waiting. */
  sync(): void {
    const next = this.next();
    if (!next) return this.hide();
    const window = this.currentWindow();
    if (!window || window.isDestroyed()) return this.hide();
    const bounds = this.bounds();
    if (!bounds) return this.hide();
    const view = this.attach(window);
    view.setBounds(bounds);
    if (this.showing === next.id) return;
    this.showing = next.id;
    const shown = {
      ...next,
      grantFolder: next.grantFolder
        ? this.displayFolder(next.grantFolder)
        : null,
    };
    void view.webContents.loadFile(
      path.join(__dirname, "../src/approval-panel.html"),
      {
        hash: encodeURIComponent(JSON.stringify(shown)),
      },
    );
  }

  reset(): void {
    this.scope = null;
    this.scoped = false;
    this.hide();
  }

  private bounds(): {
    x: number;
    y: number;
    width: number;
    height: number;
  } | null {
    const window = this.currentWindow();
    if (!window || window.isDestroyed()) return null;
    const content = window.getContentBounds();
    const zoom = window.webContents.getZoomFactor();
    if (!Number.isFinite(zoom) || zoom <= 0) return null;
    if (this.scoped && !this.scope) return null;
    const bounds = this.scope?.bounds ?? {
      x: 0,
      y: 44,
      width: content.width / zoom,
      height: content.height / zoom - 44,
    };
    const x = Math.min(content.width, Math.ceil(bounds.x * zoom));
    const y = Math.min(
      content.height,
      Math.ceil(Math.max(44, bounds.y) * zoom),
    );
    const width = Math.min(Math.floor(bounds.width * zoom), content.width - x);
    const height = Math.min(
      Math.floor((bounds.y + bounds.height) * zoom) - y,
      content.height - y,
    );
    return width >= 480 && height >= 480 ? { x, y, width, height } : null;
  }

  private matches(request: PendingApproval): boolean {
    if (
      (this.scoped && request.threadId !== this.scope?.threadId) ||
      !this.bounds()
    )
      return false;
    const window = this.currentWindow();
    if (!window || window.isDestroyed()) return false;
    try {
      const match = new URL(window.webContents.getURL()).pathname.match(
        /^\/agents\/([^/]+)\/?$/,
      );
      return Boolean(
        match && decodeURIComponent(match[1]) === request.threadId,
      );
    } catch {
      return false;
    }
  }

  private next(): PendingApproval | undefined {
    for (const id of this.later) if (!this.queue.get(id)) this.later.delete(id);
    if (this.chosen) {
      const chosen = this.queue.get(this.chosen);
      if (chosen && this.matches(chosen)) return chosen;
      this.chosen = null;
    }
    return this.queue
      .list()
      .find((request) => this.matches(request) && !this.later.has(request.id));
  }

  private attach(window: PanelHost): WebContentsView {
    if (this.view && this.window === window) return this.view;
    this.hide();
    const view = new WebContentsView({
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    view.setBackgroundColor("#00000000");
    view.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    view.webContents.on("will-navigate", (event, url) => {
      event.preventDefault();
      if (!url.startsWith(`${DECIDE_URL}?`)) return;
      const params = new URL(url).searchParams;
      const id = params.get("id") ?? "";
      const choice = params.get("choice");
      const request = this.queue.get(id);
      if (
        this.view !== view ||
        id !== this.showing ||
        !request ||
        !this.matches(request)
      )
        return;
      if (choice === "later") this.later.add(id);
      else if (choice === "deny" || choice === "once" || choice === "grant")
        this.queue.decide(id, choice);
      if (this.chosen === id) this.chosen = null;
      this.showing = null;
      this.sync();
    });
    window.once("closed", () => {
      if (this.window === window) this.hide();
    });
    window.contentView.addChildView(view);
    this.view = view;
    this.window = window;
    return view;
  }

  private hide(): void {
    if (this.view && this.window && !this.window.isDestroyed()) {
      this.window.contentView.removeChildView(this.view);
    }
    // A view's page outlives its window: close it whether or not the window is gone.
    if (this.view && !this.view.webContents.isDestroyed())
      this.view.webContents.close();
    this.view = null;
    this.window = null;
    this.showing = null;
  }
}

module.exports = { ApprovalPanel };
