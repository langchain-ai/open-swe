const { backendRequestUrl, APP_URL } = require("./config.cjs");
const { createHash } = require("node:crypto");
const { mkdir, readFile, rename, writeFile } = require("node:fs/promises");
const path = require("node:path");

type UiFetch = (url: string, init: RequestInit) => Promise<Response>;
type UiFile = { path: string; sha256: string };
type UiSnapshot = { files: UiFile[] };

function digest(body: Uint8Array | string): string {
  return createHash("sha256").update(body).digest("hex");
}

function parseSnapshot(value: unknown): UiSnapshot {
  if (
    !value ||
    typeof value !== "object" ||
    !("files" in value) ||
    !Array.isArray(value.files)
  )
    throw new Error("Invalid desktop UI manifest");
  const files: UiFile[] = value.files.map((file: unknown) => {
    if (
      !file ||
      typeof file !== "object" ||
      !("path" in file) ||
      !("sha256" in file) ||
      typeof file.path !== "string" ||
      typeof file.sha256 !== "string" ||
      !/^\/(?:assets\/)?[a-zA-Z0-9_.-]+(?:\/[a-zA-Z0-9_.-]+)*$/.test(
        file.path,
      ) ||
      (!file.path.startsWith("/assets/") && file.path.slice(1).includes("/")) ||
      file.path.includes("..") ||
      !/^[a-f0-9]{64}$/.test(file.sha256)
    )
      throw new Error("Invalid desktop UI asset");
    return { path: file.path, sha256: file.sha256 };
  });
  if (!files.some((file) => file.path === "/_shell.html"))
    throw new Error("Desktop UI manifest has no shell");
  return { files };
}

class DesktopUi {
  private snapshot: UiSnapshot | null = null;
  private update: Promise<UiSnapshot> | null = null;
  private readonly directory: string;

  constructor(
    readonly backendUrl: string,
    private readonly fetchUi: UiFetch,
    private readonly bundled: (request: Request) => Promise<Response>,
    cacheRoot: string,
  ) {
    this.directory = path.join(cacheRoot, digest(backendUrl));
  }

  async serve(request: Request): Promise<Response> {
    const navigation =
      request.mode === "navigate" ||
      request.destination === "document" ||
      request.headers.get("accept")?.includes("text/html");
    if (navigation) {
      this.snapshot = await this.readSnapshot();
      const update = this.refresh();
      if (!this.snapshot) {
        try {
          this.snapshot = await update;
        } catch (error) {
          console.warn(
            "Could not cache hosted desktop UI; using bundled UI",
            error,
          );
        }
      } else {
        void update.catch((error: unknown) =>
          console.warn(
            "Could not update desktop UI; keeping cached version",
            error,
          ),
        );
      }
    }
    if (!this.snapshot) return this.bundled(request);
    const pathname = navigation
      ? "/_shell.html"
      : new URL(request.url).pathname;
    const file = this.snapshot.files.find((entry) => entry.path === pathname);
    if (!file) return new Response("Not found", { status: 404 });
    const body = await this.readAsset(file);
    return new Response(
      request.method === "HEAD" ? null : new Uint8Array(body),
      {
        headers: {
          "content-type": this.contentType(pathname),
          "cache-control": "no-store",
        },
      },
    );
  }

  private contentType(pathname: string): string {
    const types: Record<string, string> = {
      ".html": "text/html; charset=utf-8",
      ".js": "text/javascript; charset=utf-8",
      ".css": "text/css; charset=utf-8",
      ".json": "application/json",
      ".webmanifest": "application/manifest+json",
      ".svg": "image/svg+xml",
      ".png": "image/png",
      ".ico": "image/x-icon",
      ".woff2": "font/woff2",
      ".woff": "font/woff",
      ".ttf": "font/ttf",
      ".wasm": "application/wasm",
    };
    return types[path.extname(pathname)] ?? "application/octet-stream";
  }

  private async readAsset(file: UiFile): Promise<Buffer> {
    const body: Buffer = await readFile(path.join(this.directory, file.sha256));
    if (digest(body) !== file.sha256)
      throw new Error(`Corrupt cached UI asset: ${file.path}`);
    return body;
  }

  private async readSnapshot(): Promise<UiSnapshot | null> {
    try {
      const snapshot = parseSnapshot(
        JSON.parse(
          await readFile(path.join(this.directory, "current.json"), "utf8"),
        ),
      );
      await Promise.all(snapshot.files.map((file) => this.readAsset(file)));
      return snapshot;
    } catch (error) {
      if (
        !(error instanceof Error && "code" in error && error.code === "ENOENT")
      )
        console.warn("Could not read cached desktop UI", error);
      return null;
    }
  }

  private refresh(): Promise<UiSnapshot> {
    this.update ??= this.download().finally(() => {
      this.update = null;
    });
    return this.update;
  }

  private async download(): Promise<UiSnapshot> {
    const manifest = await this.fetchHosted("/desktop-ui-manifest.json");
    const snapshot = parseSnapshot(await manifest.json());
    await mkdir(this.directory, { recursive: true });
    const pending = [
      ...new Map(snapshot.files.map((file) => [file.sha256, file])).values(),
    ];
    const results = await Promise.allSettled(
      Array.from({ length: 8 }, async () => {
        for (let file = pending.pop(); file; file = pending.pop()) {
          try {
            await this.readAsset(file);
            continue;
          } catch (error) {
            if (
              !(
                error instanceof Error &&
                "code" in error &&
                error.code === "ENOENT"
              )
            )
              console.warn("Replacing unreadable cached UI asset", error);
          }
          const response = await this.fetchHosted(file.path);
          const body = new Uint8Array(await response.arrayBuffer());
          if (digest(body) !== file.sha256)
            throw new Error(`UI asset changed during download: ${file.path}`);
          const target = path.join(this.directory, file.sha256);
          await writeFile(`${target}.tmp`, body);
          await rename(`${target}.tmp`, target);
        }
      }),
    );
    const failure = results.find((result) => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
    const target = path.join(this.directory, "current.json");
    await writeFile(`${target}.tmp`, JSON.stringify(snapshot));
    await rename(`${target}.tmp`, target);
    return snapshot;
  }

  private async fetchHosted(pathname: string): Promise<Response> {
    const response = await this.fetchUi(
      backendRequestUrl(this.backendUrl, `${APP_URL}${pathname.slice(1)}`),
      {
        method: "GET",
        redirect: "error",
        credentials: "omit",
        cache: "no-store",
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error(`Dashboard UI unavailable (${response.status})`);
    }
    return response;
  }
}

module.exports = { DesktopUi };
