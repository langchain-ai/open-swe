import path from "node:path";
import type { BaseWindow, WebContentsView } from "electron";

type Tab = WebContentsView;

const THEME_TOKENS = [
  "--bg-surface-level-1",
  "--bg-surface-level-2",
  "--bg-surface-level-2-hover",
  "--text-primary",
  "--text-secondary",
  "--border-default",
  "--radius-md",
  "--font-sans",
  "--page-background",
  "--strip-background",
] as const;

const STRIP_HEIGHT = 40;
const ACTION_URL = "robin-tabs://";

interface Options {
  createTab: () => Tab;
  createStrip: () => Tab;
  onActivate: (tab: Tab | null) => void;
  onNewTab: () => void;
  onTabsChanged: () => void;
  dark: () => boolean;
}

/**
 * A view that comes into sight before it has drawn shows white for a frame or
 * two. So the strip is drawn beneath the pages and uncovered by moving them
 * down, and a tab is drawn beneath the front one before it is raised.
 */
class AppTabs {
  readonly tabs: Tab[] = [];
  active: Tab | null = null;
  private opening: Tab | null = null;
  private strip: Tab | null = null;
  private stripReady = false;
  /** Where the pages start now; it trails `top` while the strip draws first. */
  private placedTop = 0;
  private readonly themes = new WeakMap<Tab, Record<string, string>>();
  private readonly painted = new WeakSet<Tab>();
  private readonly onPainted = new WeakMap<Tab, () => void>();
  private readonly tabbed = new WeakMap<Tab, boolean>();

  constructor(
    readonly window: BaseWindow,
    private readonly options: Options,
    private readonly platform: string = process.platform,
  ) {
    window.on("resize", () => this.layout());
    window.on("enter-full-screen", () => this.layout());
    window.on("leave-full-screen", () => this.layout());
    window.on("closed", () => {
      for (const tab of this.tabs.splice(0))
        if (!tab.webContents.isDestroyed()) tab.webContents.close();
      if (this.strip && !this.strip.webContents.isDestroyed())
        this.strip.webContents.close();
      this.strip = null;
      this.active = null;
    });
  }

  add(after: Tab | null = null): Tab {
    const tab = this.options.createTab();
    const index = after ? this.tabs.indexOf(after) : -1;
    if (index === -1) this.tabs.push(tab);
    else this.tabs.splice(index + 1, 0, tab);
    const background = this.color("--page-background");
    if (background) tab.setBackgroundColor(background);
    // Beneath every other tab, so it loads and draws out of sight.
    this.window.contentView.addChildView(tab, this.strip ? 1 : 0);
    if (!this.strip) this.createStrip();
    tab.webContents.on("page-title-updated", () => this.render());
    tab.webContents.on("did-finish-load", () => this.sendTabbed(tab, true));
    this.layout();
    this.options.onTabsChanged();
    return tab;
  }

  /** Raises a new tab once its page has drawn content, not an empty document. */
  activateWhenLoaded(tab: Tab): void {
    if (!this.active) {
      void this.activate(tab);
      return;
    }
    this.opening = tab;
    this.render();
    tab.setVisible(true);
    this.whenPainted(tab, () => {
      if (this.opening === tab) void this.activate(tab);
    });
  }

  /** Keeps a new tab behind the current page; it draws once so it is ready. */
  openInBackground(tab: Tab): void {
    if (!this.active) {
      void this.activate(tab);
      return;
    }
    this.whenPainted(tab, () => {
      if (tab !== this.active && tab !== this.opening) tab.setVisible(false);
    });
  }

  private whenPainted(tab: Tab, then: () => void): void {
    if (this.painted.has(tab)) return then();
    this.onPainted.set(tab, then);
    tab.webContents.once("did-fail-load", then);
    setTimeout(then, 5000);
  }

  contentPainted(tab: Tab): void {
    this.painted.add(tab);
    this.onPainted.get(tab)?.();
    this.onPainted.delete(tab);
  }

  activate(tab: Tab): Promise<void> {
    if (!this.tabs.includes(tab)) return Promise.resolve();
    this.opening = null;
    const changed = this.active !== tab;
    this.active = tab;
    const raised = this.raise(tab);
    this.layout();
    if (!tab.webContents.isDestroyed()) tab.webContents.focus();
    if (changed) this.options.onActivate(tab);
    return raised;
  }

  private async raise(tab: Tab): Promise<void> {
    tab.setVisible(true);
    await AppTabs.drawn(tab);
    if (this.active !== tab || this.window.isDestroyed()) return;
    this.window.contentView.addChildView(tab);
    // Hidden pages stop drawing; the one still loading keeps going.
    for (const other of this.tabs)
      if (other !== tab && other !== this.opening) other.setVisible(false);
  }

  private static drawn(view: Tab): Promise<void> {
    if (view.webContents.isDestroyed()) return Promise.resolve();
    const frame = view.webContents
      .executeJavaScript(
        "new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))",
      )
      .then(
        () => {},
        () => {},
      );
    return Promise.race([
      frame,
      new Promise<void>((done) => setTimeout(done, 250)),
    ]);
  }

  close(tab: Tab): void {
    const index = this.tabs.indexOf(tab);
    if (index === -1) return;
    if (this.tabs.length === 1) {
      this.window.close();
      return;
    }
    this.tabs.splice(index, 1);
    const contents = tab.webContents;
    if (this.opening === tab) this.opening = null;
    // The closing page stays on top until the next one is drawn over it.
    const removed =
      this.active === tab
        ? this.activate(this.tabs[Math.min(index, this.tabs.length - 1)])
        : (this.layout(), Promise.resolve());
    void removed.then(() => {
      this.window.contentView.removeChildView(tab);
      if (!contents.isDestroyed()) contents.close();
    });
    this.options.onTabsChanged();
  }

  move(tab: Tab, index: number): void {
    const from = this.tabs.indexOf(tab);
    if (from === -1 || !Number.isInteger(index)) return;
    this.tabs.splice(from, 1);
    this.tabs.splice(Math.max(0, Math.min(index, this.tabs.length)), 0, tab);
    if (this.active === tab) this.render();
    else void this.activate(tab);
  }

  cycle(step: 1 | -1): void {
    if (!this.active || this.tabs.length < 2) return;
    const index = this.tabs.indexOf(this.active);
    void this.activate(
      this.tabs[(index + step + this.tabs.length) % this.tabs.length],
    );
  }

  tabFor(contents: unknown): Tab | undefined {
    return this.tabs.find((tab) => tab.webContents === contents);
  }

  get top(): number {
    return this.tabs.length > 1 ? STRIP_HEIGHT : 0;
  }

  setTheme(tab: Tab, tokens: unknown): void {
    if (!tokens || typeof tokens !== "object") return;
    const theme: Record<string, string> = {};
    for (const name of THEME_TOKENS) {
      const value = (tokens as Record<string, unknown>)[name];
      if (name === "--page-background" || name === "--strip-background") {
        if (typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value))
          theme[name] = value;
      } else if (
        typeof value === "string" &&
        value.length <= 400 &&
        /^[\w\s#(),.%"'+/-]*$/.test(value)
      )
        theme[name] = value;
    }
    this.themes.set(tab, theme);
    if (tab !== this.active) return;
    const background = this.color("--page-background");
    if (background && !this.window.isDestroyed())
      this.window.setBackgroundColor(background);
    const stripBackground = this.color("--strip-background");
    if (stripBackground) this.strip?.setBackgroundColor(stripBackground);
    this.render();
  }

  private color(token: "--page-background" | "--strip-background") {
    return (this.active && this.themes.get(this.active))?.[token];
  }

  layout(): void {
    if (this.window.isDestroyed()) return;
    const { width } = this.window.getContentBounds();
    // Shown before it draws: a hidden page does not run animation frames.
    if (this.top > 0) this.strip?.setVisible(true);
    this.strip?.setBounds({ x: 0, y: 0, width, height: STRIP_HEIGHT });
    const drawn = this.render();
    // Uncover the strip only after it has drawn the new tab.
    if (this.top > this.placedTop && drawn)
      void drawn.then(() => this.placeTabs());
    else this.placeTabs();
  }

  private placeTabs(): void {
    if (this.window.isDestroyed()) return;
    const { width, height } = this.window.getContentBounds();
    const top = this.top;
    this.placedTop = top;
    for (const tab of this.tabs) {
      tab.setBounds({ x: 0, y: top, width, height: Math.max(0, height - top) });
      this.sendTabbed(tab);
    }
    // macOS takes a visible view's drag region even under the page, so the
    // covered strip would catch clicks on the page's title-bar buttons.
    if (top === 0) this.strip?.setVisible(false);
  }

  private sendTabbed(tab: Tab, force = false): void {
    const tabbed = this.placedTop > 0;
    if (!force && this.tabbed.get(tab) === tabbed) return;
    this.tabbed.set(tab, tabbed);
    if (!tab.webContents.isDestroyed())
      tab.webContents.send("desktop:tabbed", tabbed);
  }

  private createStrip(): void {
    const strip = this.options.createStrip();
    this.strip = strip;
    // A view shown for the first time paints this before its page draws.
    strip.setBackgroundColor(
      this.color("--strip-background") ??
        (this.options.dark() ? "#15171c" : "#f4f5f7"),
    );
    strip.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    strip.webContents.on("will-navigate", (event, url) => {
      event.preventDefault();
      this.act(url);
    });
    strip.webContents.on("did-finish-load", () => {
      this.stripReady = true;
      this.render();
    });
    this.window.contentView.addChildView(strip, 0);
    void strip.webContents.loadFile(
      path.join(__dirname, "../src/tab-strip.html"),
    );
  }

  private act(url: string): void {
    if (!url.startsWith(ACTION_URL)) return;
    const action = new URL(url);
    if (action.hostname === "new") return this.options.onNewTab();
    const tab = this.tabs.find(
      (candidate) =>
        String(candidate.webContents.id) === action.searchParams.get("id"),
    );
    if (!tab) return;
    if (action.hostname === "activate") void this.activate(tab);
    else if (action.hostname === "close") this.close(tab);
    else if (action.hostname === "move")
      this.move(tab, Number(action.searchParams.get("index")));
  }

  private render(): Promise<void> | null {
    const strip = this.strip;
    if (!strip || !this.stripReady || strip.webContents.isDestroyed())
      return null;
    const state = {
      inset: this.platform === "darwin" && !this.window.isFullScreen() ? 92 : 8,
      theme: (this.active && this.themes.get(this.active)) ?? {},
      tabs: this.tabs.map((tab) => ({
        id: tab.webContents.id,
        title: AppTabs.title(tab.webContents.getTitle()),
        active: tab === (this.opening ?? this.active),
      })),
    };
    return strip.webContents
      .executeJavaScript(`render(${JSON.stringify(state)})`)
      .then(
        () => {},
        () => {},
      );
  }

  static title(value: string): string {
    const title = value.replace(/\s+·\s+Robin(?: Development)?$/, "").trim();
    return title || "Robin";
  }
}

module.exports = { AppTabs, THEME_TOKENS };
