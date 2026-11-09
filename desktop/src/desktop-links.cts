const { desktopDeepLinkUrl } = require("./config.cjs");

type DesktopLinkHandlers = {
  backendUrl: () => string | null;
  /** The origins the backend says serve its dashboard; may need a request to learn. */
  dashboardOrigins: (backendUrl: string) => Promise<readonly string[]>;
  hasPage: (pathname: string, backendUrl: string) => Promise<boolean>;
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

  private open(url: string): boolean {
    const backendUrl = this.handlers.backendUrl();
    void this.openOnDashboardOrigin(url, backendUrl);
    return false;
  }

  async appUrl(
    url: string,
    backendUrl = this.handlers.backendUrl(),
  ): Promise<string | null> {
    if (
      !backendUrl ||
      !URL.canParse(url) ||
      !["http:", "https:"].includes(new URL(url).protocol)
    )
      return null;
    const target =
      desktopDeepLinkUrl(url, backendUrl) ??
      desktopDeepLinkUrl(
        url,
        backendUrl,
        await this.handlers.dashboardOrigins(backendUrl).catch(() => []),
      );
    if (!target) return null;
    try {
      return (await this.handlers.hasPage(new URL(url).pathname, backendUrl))
        ? target
        : null;
    } catch (error) {
      console.warn("Could not resolve desktop UI route", error);
      return null;
    }
  }

  private async openOnDashboardOrigin(
    url: string,
    backendUrl: string | null,
  ): Promise<void> {
    const target = await this.appUrl(url, backendUrl);
    if (this.handlers.backendUrl() !== backendUrl) return;
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
