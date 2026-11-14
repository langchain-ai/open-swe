import path from "node:path";
import { type BrowserWindow, WebContentsView } from "electron";

type Decision = "deny" | "once" | "grant";

interface PendingApproval {
  id: string;
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

/**
 * The desktop's own approval surface, laid over the app window: a page bundled with
 * the app, not the dashboard the backend serves, so nothing the backend or the page
 * sends can answer it. Only a person choosing in it decides a local action.
 */
class ApprovalPanel {
  private view: WebContentsView | null = null;
  private window: BrowserWindow | null = null;
  private fit: (() => void) | null = null;
  private showing: string | null = null;
  /** Requests the person put off; they stay pending until decided or expired. */
  private readonly later = new Set<string>();
  /** A request the person asked to review from its thread, shown before the others. */
  private chosen: string | null = null;

  constructor(
    private readonly queue: Queue,
    private readonly currentWindow: () => BrowserWindow | null,
    private readonly displayFolder: (folder: string) => string,
  ) {}

  /** Opens the panel on `id`, as a thread's Review button asks. */
  review(id: string): void {
    if (!this.queue.get(id)) return;
    this.later.delete(id);
    this.chosen = id;
    this.sync();
  }

  /** Shows the request to decide now, or hides the panel when none is waiting. */
  sync(): void {
    const next = this.next();
    if (!next) return this.hide();
    const window = this.currentWindow();
    if (!window || window.isDestroyed()) return;
    const view = this.attach(window);
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
    window.focus();
    view.webContents.focus();
  }

  private next(): PendingApproval | undefined {
    for (const id of this.later) if (!this.queue.get(id)) this.later.delete(id);
    if (this.chosen) {
      const chosen = this.queue.get(this.chosen);
      if (chosen) return chosen;
      this.chosen = null;
    }
    return this.queue.list().find((request) => !this.later.has(request.id));
  }

  private attach(window: BrowserWindow): WebContentsView {
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
      if (choice === "later") this.later.add(id);
      else if (choice === "deny" || choice === "once" || choice === "grant")
        this.queue.decide(id, choice);
      if (this.chosen === id) this.chosen = null;
      this.showing = null;
      this.sync();
    });
    const fit = () => {
      const { width, height } = window.getContentBounds();
      view.setBounds({ x: 0, y: 0, width, height });
    };
    fit();
    window.on("resize", fit);
    window.once("closed", () => {
      if (this.window === window) this.hide();
    });
    this.fit = fit;
    window.contentView.addChildView(view);
    this.view = view;
    this.window = window;
    return view;
  }

  private hide(): void {
    if (this.view && this.window && !this.window.isDestroyed()) {
      if (this.fit) this.window.off("resize", this.fit);
      this.window.contentView.removeChildView(this.view);
    }
    // A view's page outlives its window: close it whether or not the window is gone.
    if (this.view && !this.view.webContents.isDestroyed())
      this.view.webContents.close();
    this.fit = null;
    this.view = null;
    this.window = null;
    this.showing = null;
  }
}

module.exports = { ApprovalPanel };
