const { backendRequestUrl, APP_URL } = require("./config.cjs");

type UiFetch = (url: string, init: RequestInit) => Promise<Response>;

class DesktopUi {
  private hosted = false;

  constructor(
    readonly backendUrl: string,
    private readonly fetchUi: UiFetch,
    private readonly bundled: (request: Request) => Promise<Response>,
  ) {}

  async serve(request: Request): Promise<Response> {
    const navigation =
      request.mode === "navigate" ||
      request.destination === "document" ||
      request.headers.get("accept")?.includes("text/html");
    if (navigation) {
      try {
        const response = await this.fetchHosted(
          `${APP_URL}_shell.html`,
          request.method,
        );
        if (
          !response.ok ||
          !response.headers.get("content-type")?.includes("text/html")
        ) {
          await response.body?.cancel();
          throw new Error(`Dashboard shell unavailable (${response.status})`);
        }
        this.hosted = true;
        return response;
      } catch (error) {
        console.warn(
          "Could not load hosted desktop UI; using bundled UI",
          error,
        );
        this.hosted = false;
      }
    }
    return this.hosted
      ? this.fetchHosted(request.url, request.method)
      : this.bundled(request);
  }

  private async fetchHosted(url: string, method: string): Promise<Response> {
    const response = await this.fetchUi(
      backendRequestUrl(this.backendUrl, url),
      {
        method,
        redirect: "error",
        credentials: "omit",
        cache: "no-store",
        signal: AbortSignal.timeout(10_000),
      },
    );
    const headers = new Headers(response.headers);
    headers.delete("content-encoding");
    headers.delete("content-length");
    headers.delete("set-cookie");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  }
}

module.exports = { DesktopUi };
