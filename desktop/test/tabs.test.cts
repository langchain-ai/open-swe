const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const Module = require("node:module");

// tabs.cjs only takes types from electron; keep the real module out of node.
const originalLoad = Module._load;
Module._load = function (request: string, ...rest: unknown[]) {
  if (request === "electron") return {};
  return Reflect.apply(originalLoad, this, [request, ...rest]);
};
const { AppTabs } = require("../build/tabs.cjs");
Module._load = originalLoad;

let nextId = 1;

class FakeContents extends EventEmitter {
  id = nextId++;
  title = "Robin";
  destroyed = false;
  sent: unknown[][] = [];
  scripts: string[] = [];
  focused = 0;
  getTitle() {
    return this.title;
  }
  isDestroyed() {
    return this.destroyed;
  }
  close() {
    this.destroyed = true;
  }
  focus() {
    this.focused++;
  }
  send(...args: unknown[]) {
    this.sent.push(args);
  }
  setWindowOpenHandler() {}
  async loadFile() {
    this.emit("did-finish-load");
  }
  async executeJavaScript(script: string) {
    this.scripts.push(script);
  }
}

class FakeView {
  webContents = new FakeContents();
  bounds = { x: 0, y: 0, width: 0, height: 0 };
  visible = true;
  background = "";
  setBackgroundColor(color: string) {
    this.background = color;
  }
  setBounds(bounds: FakeView["bounds"]) {
    this.bounds = bounds;
  }
  setVisible(visible: boolean) {
    this.visible = visible;
  }
}

class FakeWindow extends EventEmitter {
  views: FakeView[] = [];
  closed = false;
  fullScreen = false;
  // Bottom to top, as the window stacks them; adding a view again raises it.
  contentView = {
    addChildView: (view: FakeView, index?: number) => {
      this.views = this.views.filter((candidate) => candidate !== view);
      this.views.splice(index ?? this.views.length, 0, view);
    },
    removeChildView: (view: FakeView) => {
      this.views = this.views.filter((candidate) => candidate !== view);
    },
  };
  isDestroyed() {
    return this.closed;
  }
  isFullScreen() {
    return this.fullScreen;
  }
  getContentBounds() {
    return { x: 0, y: 0, width: 1200, height: 800 };
  }
  close() {
    this.closed = true;
  }
  setBackgroundColor() {}
}

function harness() {
  const window = new FakeWindow();
  const strips: FakeView[] = [];
  const events = {
    activated: [] as unknown[],
    added: 0,
    newTabs: 0,
    menus: [] as { label: string; click: () => void }[][],
  };
  const tabs = new AppTabs(
    window,
    {
      createTab: () => new FakeView(),
      createStrip: () => {
        const strip = new FakeView();
        strips.push(strip);
        return strip;
      },
      onActivate: (tab: unknown) => events.activated.push(tab),
      onNewTab: () => events.newTabs++,
      onTabsChanged: () => events.added++,
      showMenu: (items: (typeof events.menus)[number]) =>
        events.menus.push(items),
      dark: () => true,
    },
    "darwin",
  );
  const lastState = () => {
    const script = strips[0]?.webContents.scripts.at(-1) ?? "";
    return JSON.parse(script.replace(/^render\(/, "").replace(/\)$/, ""));
  };
  return { window, tabs, strips, events, lastState };
}

const settle = () => new Promise(setImmediate);

test("a new tab draws out of sight and only then covers the page", async () => {
  const { tabs, strips, window } = harness();
  const first = tabs.add();
  await tabs.activate(first);
  await settle();
  assert.deepEqual(window.views, [strips[0], first]);
  // Hidden under one tab, or its drag region catches the page's header clicks.
  assert.equal(strips[0].visible, false);
  assert.deepEqual(strips[0].bounds, { x: 0, y: 0, width: 1200, height: 40 });
  assert.deepEqual(first.bounds, { x: 0, y: 0, width: 1200, height: 800 });

  tabs.setTheme(first, {
    "--page-background": "#101114",
    "--strip-background": "#0c0d10",
  });
  assert.equal(strips[0].background, "#0c0d10");
  const second = tabs.add(first);
  assert.equal(second.background, "#101114");
  tabs.activateWhenLoaded(second);
  await settle();
  // Loading beneath the current page, which stays in front and moves down.
  assert.deepEqual(window.views, [strips[0], second, first]);
  assert.equal(second.visible, true);
  assert.equal(strips[0].visible, true);
  assert.deepEqual(first.bounds, { x: 0, y: 40, width: 1200, height: 760 });
  assert.deepEqual(first.webContents.sent.at(-1), ["desktop:tabbed", true]);

  // Loaded but still empty: the current page stays in front.
  second.webContents.emit("did-finish-load");
  await settle();
  assert.equal(tabs.active, first);
  tabs.contentPainted(second);
  await settle();
  assert.equal(tabs.active, second);
  assert.equal(window.views.at(-1), second);
  assert.equal(first.visible, false);

  tabs.close(second);
  await settle();
  assert.equal(tabs.active, first);
  assert.equal(second.webContents.destroyed, true);
  assert.deepEqual(window.views, [strips[0], first]);
  assert.deepEqual(first.bounds, { x: 0, y: 0, width: 1200, height: 800 });
  assert.deepEqual(first.webContents.sent.at(-1), ["desktop:tabbed", false]);
  assert.equal(strips[0].visible, false);
});

test("a link opened in a new tab stays behind the current page", async () => {
  const { tabs, window, strips, lastState } = harness();
  const first = tabs.add();
  await tabs.activate(first);
  const link = tabs.add(first);
  tabs.openInBackground(link);
  tabs.contentPainted(link);
  await settle();
  assert.equal(tabs.active, first);
  assert.equal(window.views.at(-1), first);
  assert.equal(link.visible, false);
  assert.deepEqual(
    lastState().tabs.map((tab: { active: boolean }) => tab.active),
    [true, false],
  );
  assert.equal(strips.length, 1);
});

test("new tabs open after their opener and cycling wraps", () => {
  const { tabs } = harness();
  const a = tabs.add();
  const b = tabs.add(a);
  const c = tabs.add(a);
  assert.deepEqual(tabs.tabs, [a, c, b]);
  tabs.activate(b);
  tabs.cycle(1);
  assert.equal(tabs.active, a);
  tabs.cycle(-1);
  assert.equal(tabs.active, b);
  tabs.select(0);
  assert.equal(tabs.active, a);
  tabs.select(-1);
  assert.equal(tabs.active, b);
  tabs.select(5);
  assert.equal(tabs.active, b);
});

test("closing other tabs from a tab's menu keeps that tab in front", async () => {
  const { tabs, strips, events, window } = harness();
  const a = tabs.add();
  const b = tabs.add(a);
  const c = tabs.add(b);
  await tabs.activate(b);
  strips[0].webContents.emit(
    "will-navigate",
    { preventDefault() {} },
    `robin-tabs://menu?id=${a.webContents.id}`,
  );
  const items = events.menus.at(-1) ?? [];
  items.find((item) => item.label === "Close Other Tabs")?.click();
  assert.deepEqual(tabs.tabs, [a]);
  assert.equal(tabs.active, a);
  await settle();
  assert.equal(b.webContents.destroyed, true);
  assert.equal(c.webContents.destroyed, true);
  assert.deepEqual(window.views, [strips[0], a]);
  assert.equal(window.closed, false);
});

test("the tab bar's own menu closes the tabs behind the front one", async () => {
  const { tabs, strips, events } = harness();
  const a = tabs.add();
  const b = tabs.add(a);
  await tabs.activate(b);
  strips[0].webContents.emit(
    "will-navigate",
    { preventDefault() {} },
    "robin-tabs://menu",
  );
  const items = events.menus.at(-1) ?? [];
  assert.deepEqual(
    items.map((item) => item.label),
    ["Close Other Tabs"],
  );
  items[0].click();
  assert.deepEqual(tabs.tabs, [b]);
});

test("closing the last tab closes the window", () => {
  const { tabs, window } = harness();
  const only = tabs.add();
  tabs.activate(only);
  tabs.close(only);
  assert.equal(window.closed, true);
});

test("the strip lists tabs, takes clicks, and keeps only safe theme values", () => {
  const { tabs, strips, events, lastState } = harness();
  const a = tabs.add();
  a.webContents.title = "refactor(skills): docs router · Robin";
  const b = tabs.add(a);
  tabs.activate(a);
  tabs.setTheme(a, {
    "--text-primary": "oklch(0.2 0 0)",
    "--bg-surface-level-1": "red; } body { display: none",
    "--unknown": "#fff",
  });
  const state = lastState();
  assert.equal(state.inset, 92);
  assert.deepEqual(
    state.tabs.map((tab: { title: string; active: boolean }) => [
      tab.title,
      tab.active,
    ]),
    [
      ["refactor(skills): docs router", true],
      ["Robin", false],
    ],
  );
  assert.deepEqual(state.theme, { "--text-primary": "oklch(0.2 0 0)" });

  const navigate = (url: string) => {
    let prevented = false;
    strips[0].webContents.emit(
      "will-navigate",
      { preventDefault: () => (prevented = true) },
      url,
    );
    return prevented;
  };
  assert.equal(navigate(`robin-tabs://activate?id=${b.webContents.id}`), true);
  assert.equal(tabs.active, b);
  // Dropping a dragged tab also selects it.
  navigate(`robin-tabs://move?id=${a.webContents.id}&index=0`);
  assert.equal(tabs.active, a);
  assert.deepEqual(tabs.tabs, [a, b]);
  void tabs.activate(b);
  navigate(`robin-tabs://move?id=${a.webContents.id}&index=1`);
  assert.deepEqual(tabs.tabs, [b, a]);
  navigate(`robin-tabs://move?id=${a.webContents.id}&index=nope`);
  assert.deepEqual(tabs.tabs, [b, a]);
  navigate("robin-tabs://new");
  assert.equal(events.newTabs, 1);
  navigate(`robin-tabs://close?id=${a.webContents.id}`);
  assert.deepEqual(tabs.tabs, [b]);
  assert.equal(navigate("https://example.com/"), true);
});
