const { desktopDeepLinkUrl } = require("./config.cjs");

type DesktopLinkHandlers = {
  backendUrl: () => string | null;
  /** The origins the backend says serve its dashboard; may need a request to learn. */
  dashboardOrigins: (backendUrl: string) => Promise<readonly string[]>;
  hasPage: (pathname: string, backendUrl: string) => Promise<boolean>;
  openApp: (url: string) => void;
  openExternal: (url: string) => Promise<unknown>;
  fetchBackend: (url: string, init: RequestInit) => Promise<Response>;
};

export class DesktopLinks {
  private pendingUrl: string | null = null;
  private initialized = false;

  constructor(private readonly handlers: DesktopLinkHandlers) {}

  slackUrl(value: string): string | null {
    const backendUrl = this.handlers.backendUrl();
    if (!backendUrl || !URL.canParse(value)) return null;
    const source = new URL(value);
    const backend = new URL(backendUrl);
    if (
      source.pathname !== "/dashboard/api/slack/open" ||
      source.username ||
      source.password ||
      !(
        source.origin === backend.origin ||
        (source.protocol === "open-swe:" &&
          source.hostname === "app" &&
          !source.port)
      )
    )
      return null;
    const target = new URL("/dashboard/api/slack/open", backend);
    target.search = source.search;
    return target.toString();
  }

  async openSlack(value: string): Promise<void> {
    const url = this.slackUrl(value);
    if (!url) throw new Error("Invalid Slack link");
    const response = await this.handlers.fetchBackend(url, {
      redirect: "manual",
      signal: AbortSignal.timeout(15_000),
    });
    const location = response.headers.get("location");
    if (response.status !== 302 || !location || !URL.canParse(location))
      throw new Error(`Could not resolve Slack link (${response.status})`);
    const target = new URL(location);
    if (
      target.protocol !== "https:" ||
      !/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.slack\.com$/.test(target.hostname) ||
      target.hostname === "app.slack.com" ||
      target.username ||
      target.password ||
      target.port ||
      target.hash ||
      !/^\/archives\/[CGD][A-Z0-9]{2,}(?:\/p[0-9]+)?$/.test(target.pathname)
    )
      throw new Error("Backend returned an invalid Slack destination");
    await this.handlers.openExternal(target.toString());
  }

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
