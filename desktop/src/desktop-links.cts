const { desktopDeepLinkUrl } = require("./config.cjs");

type DesktopLinkHandlers = {
  backendUrl: () => string | null;
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

  ready(): boolean {
    this.initialized = true;
    const url = this.pendingUrl;
    this.pendingUrl = null;
    return url ? this.open(url) : false;
  }

  private open(url: string): boolean {
    const backendUrl = this.handlers.backendUrl();
    const target = backendUrl && desktopDeepLinkUrl(url, backendUrl);
    if (!target) {
      if (URL.canParse(url) && new URL(url).protocol === "https:") {
        void this.handlers
          .openExternal(url)
          .catch((error: unknown) =>
            console.warn("Could not open desktop link in the browser", error),
          );
      }
      return false;
    }
    this.handlers.openApp(target);
    return true;
  }
}
