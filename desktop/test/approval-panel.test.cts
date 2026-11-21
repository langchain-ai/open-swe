const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");
const { ApprovalQueue } = require("../build/approval-queue.cjs");

class PanelContents extends EventEmitter {
  closed = false;
  shown: { id: string; threadId: string } | null = null;
  setWindowOpenHandler() {}
  async loadFile(_file: string, options: { hash: string }) {
    this.shown = JSON.parse(decodeURIComponent(options.hash));
  }
  isDestroyed() {
    return this.closed;
  }
  close() {
    this.closed = true;
  }
  focus() {
    throw new Error("Approval must not steal focus");
  }
  choose(id: string, choice: string) {
    this.emit(
      "will-navigate",
      { preventDefault() {} },
      `open-swe-approval://decide?id=${id}&choice=${choice}`,
    );
  }
}

class PanelView {
  webContents = new PanelContents();
  bounds: { x: number; y: number; width: number; height: number } | null = null;
  setBackgroundColor() {}
  setBounds(bounds: NonNullable<PanelView["bounds"]>) {
    this.bounds = bounds;
  }
}

class PanelWindow extends EventEmitter {
  url = "open-swe://app/agents/one";
  views = new Set<PanelView>();
  zoom = 1;
  height = 700;
  webContents = { getURL: () => this.url, getZoomFactor: () => this.zoom };
  contentView = {
    addChildView: (view: PanelView) => this.views.add(view),
    removeChildView: (view: PanelView) => this.views.delete(view),
  };
  isDestroyed() {
    return false;
  }
  getContentBounds() {
    return { width: 1000, height: this.height };
  }
  focus() {
    throw new Error("Approval must not steal focus");
  }
  current(): PanelView {
    return [...this.views][0];
  }
}

class PanelHarness {
  window = new PanelWindow();
  queue = new ApprovalQueue(() => this.panel.sync());
  panel: {
    setThread(threadId: unknown, bounds: unknown): boolean;
    sync(): void;
    reset(): void;
    review(id: string): void;
  };
  bounds = { x: 250, y: 44, width: 750, height: 656 };

  constructor() {
    const module = { exports: {} };
    runInNewContext(
      readFileSync(require.resolve("../build/approval-panel.cjs"), "utf8"),
      {
        module,
        exports: module.exports,
        __dirname: __dirname + "/../build",
        URL,
        require: (name: string) =>
          name === "electron" ? { WebContentsView: PanelView } : require(name),
      },
    );
    const { ApprovalPanel } = module.exports as {
      ApprovalPanel: new (...args: unknown[]) => PanelHarness["panel"];
    };
    this.panel = new ApprovalPanel(
      this.queue,
      () => this.window,
      (folder: string) => folder,
    );
    this.window.on("resize", () => this.panel.sync());
  }

  request(threadId: string | null) {
    const abort = new AbortController();
    const result = this.queue.request(
      {
        kind: "execute",
        threadId,
        threadTitle: threadId,
        concern: null,
        detail: "echo approved",
        grantFolder: null,
      },
      abort.signal,
    );
    const request = this.queue.list().at(-1);
    return { ...request, result, abort };
  }
}

test("only the mounted thread's approvals display, clipped without stealing focus", () => {
  const harness = new PanelHarness();
  harness.panel.setThread("one", null);
  harness.request("two");
  harness.request(null);
  const one = harness.request("one");
  harness.request("one");
  assert.equal(harness.window.views.size, 0);
  assert.deepEqual(harness.queue.pendingThreads(), ["two", "one"]);
  harness.panel.setThread("one", harness.bounds);
  const view = harness.window.current();
  assert.equal(view.webContents.shown?.id, one.id);
  assert.deepEqual({ ...view.bounds }, harness.bounds);
  harness.panel.setThread("one", {
    ...harness.bounds,
    width: 2000,
    height: 2000,
  });
  assert.deepEqual({ ...view.bounds }, harness.bounds);
  harness.window.zoom = 1.25;
  harness.panel.setThread("one", {
    x: 200,
    y: 35.2,
    width: 600,
    height: 524.8,
  });
  assert.deepEqual(
    { ...view.bounds },
    { ...harness.bounds, y: 55, height: 645 },
  );
  harness.panel.review(harness.queue.list("two")[0].id);
  assert.equal(view.webContents.shown?.id, one.id);
  assert.equal(harness.queue.list().length, 4);
  for (const bounds of [
    { ...harness.bounds, x: NaN },
    { ...harness.bounds, width: 0 },
    {},
  ])
    assert.equal(harness.panel.setThread("one", bounds), false);
  harness.queue.clear();
  harness.window.zoom = 1;
  harness.panel.setThread("one", harness.bounds);
  harness.request("two");
  assert.equal(harness.window.views.size, 0);
  const incoming = harness.request("one");
  assert.equal(harness.window.current().webContents.shown?.id, incoming.id);
  harness.queue.clear();
});

test("cached dashboards retain route-local approvals until scope registration", () => {
  const harness = new PanelHarness();
  harness.request("two");
  const request = harness.request("one");
  assert.equal(harness.window.current().webContents.shown?.id, request.id);
  assert.deepEqual(
    { ...harness.window.current().bounds },
    {
      x: 0,
      y: 44,
      width: 1000,
      height: 656,
    },
  );
  harness.window.zoom = 3;
  harness.window.height = 600;
  harness.window.emit("resize");
  assert.equal(harness.window.views.size, 0);
  assert.equal(harness.queue.list().length, 2);
  harness.window.height = 900;
  harness.window.emit("resize");
  assert.equal(harness.window.current().webContents.shown?.id, request.id);
  harness.panel.setThread("one", null);
  harness.panel.review(request.id);
  assert.equal(harness.window.views.size, 0);
  harness.panel.reset();
  harness.panel.sync();
  assert.equal(harness.window.current().webContents.shown?.id, request.id);
  harness.window.url = "open-swe://app/agents/plugins/example";
  harness.panel.sync();
  assert.equal(harness.window.views.size, 0);
  harness.queue.clear();
});

test("tiny or cropped scopes hide the trusted panel and cannot decide", () => {
  const harness = new PanelHarness();
  const request = harness.request("one");
  for (const bounds of [
    { ...harness.bounds, width: 1 },
    { ...harness.bounds, height: 1 },
    { ...harness.bounds, x: 999 },
    { ...harness.bounds, y: 699 },
  ]) {
    harness.panel.setThread("one", harness.bounds);
    const previous = harness.window.current();
    harness.panel.setThread("one", bounds);
    assert.equal(harness.window.views.size, 0);
    previous.webContents.choose(request.id, "once");
    assert.equal(harness.queue.list().length, 1);
  }
  harness.panel.setThread("one", { x: 0, y: 0, width: 1000, height: 700 });
  assert.deepEqual(
    { ...harness.window.current().bounds },
    {
      x: 0,
      y: 44,
      width: 1000,
      height: 656,
    },
  );
  harness.window.zoom = 0.5;
  harness.window.emit("resize");
  assert.equal(harness.window.views.size, 0);
  harness.queue.clear();
});

test("navigation and stale cleanup cannot show or decide another thread's request", async () => {
  const harness = new PanelHarness();
  const one = harness.request("one");
  const two = harness.request("two");
  harness.panel.setThread("one", harness.bounds);
  const old = harness.window.current();
  harness.window.url = "open-swe://app/agents/two";
  harness.panel.sync();
  assert.equal(old.webContents.closed, true);
  assert.equal(harness.window.views.size, 0);
  harness.panel.review(one.id);
  assert.equal(harness.window.views.size, 0);
  harness.panel.setThread("two", harness.bounds);
  harness.panel.setThread("one", null);
  const current = harness.window.current();
  assert.equal(current.webContents.shown?.id, two.id);
  old.webContents.choose(one.id, "once");
  old.webContents.choose(two.id, "once");
  current.webContents.choose(one.id, "grant");
  assert.equal(harness.queue.list().length, 2);
  current.webContents.choose(two.id, "once");
  assert.equal(await two.result, "once");
  assert.equal(harness.window.views.size, 0);
  one.abort.abort();
  assert.equal(await one.result, "deny");
});

test("exact encoded thread routes gate display; Later and expiry preserve queue policy", async () => {
  const harness = new PanelHarness();
  const request = harness.request("thread name");
  for (const url of [
    "/agents/thread%20name/plan",
    "/agents/settings",
    "/agents/thread%ZZname",
  ]) {
    harness.window.url = "open-swe://app" + url;
    harness.panel.setThread("thread name", harness.bounds);
    assert.equal(harness.window.views.size, 0);
  }
  harness.window.url = "open-swe://app/agents/thread%20name?view=chat";
  harness.panel.sync();
  harness.window.current().webContents.choose(request.id, "later");
  assert.equal(harness.window.views.size, 0);
  assert.equal(harness.queue.list().length, 1);
  harness.panel.review(request.id);
  assert.equal(harness.window.current().webContents.shown?.id, request.id);
  request.abort.abort();
  assert.equal(await request.result, "deny");
  assert.equal(harness.window.views.size, 0);
  assert.deepEqual(harness.queue.pendingThreads(), []);
});
