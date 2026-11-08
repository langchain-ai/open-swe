const { desktopDeepLinkUrl } = require("./config.cjs");

type DesktopLinkHandlers = {
  backendUrl: () => string | null;
  /** The origins the backend says serve its dashboard; may need a request to learn. */
  dashboardOrigins: (backendUrl: string) => Promise<readonly string[]>;
  openApp: (url: string) => void;
  openExternal: (url: string) => Promise<unknown>;
};

export class DesktopLinks {
  private pendingUrl: string | null = null;
  private initialized = false;

  constructor(private readonly handlers: DesktopLinkHandlers) {}

  receive(url: string): void {
    if (!this.initialized) this.pendingUrl = url;
    else this.open(url);
  }

  /** Opens the link that launched the app; false when the caller should open a window. */
  ready(): boolean {
    this.initialized = true;
    const url = this.pendingUrl;
    this.pendingUrl = null;
    return url ? this.open(url) : false;
  }

  /**
   * True when the link opened in the app right away. A link on another origin
   * waits for the backend's dashboard origins, then opens in the app if it is one
   * of them (robin.langchain.dev while the app points at openswe.langchain.dev, say)
   * or in the browser otherwise.
   */
  private open(url: string): boolean {
    const backendUrl = this.handlers.backendUrl();
    const target = backendUrl && desktopDeepLinkUrl(url, backendUrl);
    if (target) {
      this.handlers.openApp(target);
      return true;
    }
    void this.openOnDashboardOrigin(url, backendUrl);
    return false;
  }

  private async openOnDashboardOrigin(
    url: string,
    backendUrl: string | null,
  ): Promise<void> {
    const origins = backendUrl
      ? await this.handlers.dashboardOrigins(backendUrl).catch(() => [])
      : [];
    const target =
      backendUrl &&
      origins.length > 0 &&
      desktopDeepLinkUrl(url, backendUrl, origins);
    if (target) {
      this.handlers.openApp(target);
      return;
    }
    if (URL.canParse(url) && new URL(url).protocol === "https:") {
      await this.handlers
        .openExternal(url)
        .catch((error: unknown) =>
          console.warn("Could not open desktop link in the browser", error),
        );
    }
  }
}
