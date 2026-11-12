const { desktopEnvironmentsUrl } = require("./config.cjs");

type Fetch = (url: string, init?: RequestInit) => Promise<Response>;
type Options = { fetch?: Fetch; now?: () => number; maxAgeMs?: number };
type Cached = { response: Promise<unknown>; at: number };

/** How long one backend's answer stands before the next link asks again. */
const MAX_AGE_MS = 10 * 60_000;

/**
 * What each backend's `/dashboard/api/desktop/environments` says, by backend URL:
 * its alternate environments and the origins that serve its dashboard (its
 * canonical domain and aliases), whose links the app opens as its own.
 *
 * Answers are reused for `maxAgeMs`, so a backend that changes its domains is
 * picked up without restarting the app. A failed request is dropped right away.
 */
export class DesktopEnvironments {
  private readonly cached = new Map<string, Cached>();
  private readonly origins = new Map<string, readonly string[]>();
  private readonly fetch: Fetch;
  private readonly now: () => number;
  private readonly maxAgeMs: number;

  constructor(options: Options = {}) {
    this.fetch = options.fetch ?? fetch;
    this.now = options.now ?? Date.now;
    this.maxAgeMs = options.maxAgeMs ?? MAX_AGE_MS;
  }

  /** The origins in a response's `dashboard_origins`, skipping any that are malformed. */
  static parseDashboardOrigins(payload: unknown): string[] {
    const entries = (payload as { dashboard_origins?: unknown } | null)
      ?.dashboard_origins;
    if (!Array.isArray(entries)) return [];
    return entries.flatMap((entry) => {
      if (typeof entry !== "string") return [];
      try {
        const url = new URL(entry);
        return ["http:", "https:"].includes(url.protocol) ? [url.origin] : [];
      } catch {
        return [];
      }
    });
  }

  /** `path` on the dashboard's public base URL, falling back to `backendUrl`; keeps any base path prefix. */
  static dashboardPageUrl(
    payload: unknown,
    backendUrl: string,
    path: string,
  ): string {
    if (!path.startsWith("/") || path.startsWith("//"))
      throw new Error("Invalid dashboard path");
    const advertised = (payload as { dashboard_base_url?: unknown } | null)
      ?.dashboard_base_url;
    let base = new URL(backendUrl);
    if (typeof advertised === "string" && URL.canParse(advertised)) {
      const candidate = new URL(advertised);
      if (["http:", "https:"].includes(candidate.protocol)) base = candidate;
    }
    base.search = "";
    base.hash = "";
    if (!base.pathname.endsWith("/")) base.pathname += "/";
    const target = new URL(path.slice(1), base);
    if (target.origin !== base.origin)
      throw new Error("Invalid dashboard path");
    return target.toString();
  }

  /** `path` on the dashboard `backendUrl` advertises, or on `backendUrl` when it cannot say. */
  async dashboardPageUrl(backendUrl: string, path: string): Promise<string> {
    const payload = await this.response(backendUrl).catch(() => null);
    return DesktopEnvironments.dashboardPageUrl(payload, backendUrl, path);
  }

  /** The backend's answer, asked again once the last one is older than `maxAgeMs`. */
  response(backendUrl: string): Promise<unknown> {
    const cached = this.cached.get(backendUrl);
    if (cached && this.now() - cached.at < this.maxAgeMs)
      return cached.response;
    const response = this.fetch(desktopEnvironmentsUrl(backendUrl), {
      signal: AbortSignal.timeout(10_000),
    }).then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    });
    const entry = { response, at: this.now() };
    this.cached.set(backendUrl, entry);
    response.then(
      (payload) => {
        this.origins.set(
          backendUrl,
          DesktopEnvironments.parseDashboardOrigins(payload),
        );
      },
      (error: unknown) => {
        if (this.cached.get(backendUrl) === entry)
          this.cached.delete(backendUrl);
        console.warn("Could not read the desktop environments", error);
      },
    );
    return response;
  }

  /** The origins `backendUrl` says serve its dashboard, asking it if needed. */
  async dashboardOrigins(backendUrl: string): Promise<readonly string[]> {
    return DesktopEnvironments.parseDashboardOrigins(
      await this.response(backendUrl),
    );
  }

  /** The origins from the backend's last answer, without asking; empty until it answers. */
  knownDashboardOrigins(backendUrl: string): readonly string[] {
    return this.origins.get(backendUrl) ?? [];
  }
}
