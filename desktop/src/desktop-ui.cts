const { backendRequestUrl, APP_URL } = require("./config.cjs");
const { createHash } = require("node:crypto");
const { mkdir, readFile, rename, writeFile } = require("node:fs/promises");
const path = require("node:path");
const { gunzipSync } = require("node:zlib");

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
          throw new Error(
            "Connect to the internet to download the Open SWE UI, then use View → Reload. " +
              "Check Open SWE → Backend URL… if the server is unavailable.",
            { cause: error },
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
    if (!this.snapshot)
      return new Response("UI has not been downloaded", { status: 503 });
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
    const response = await this.fetchHosted("/desktop-ui.json");
    const descriptor: unknown = await response.json();
    if (
      !descriptor ||
      typeof descriptor !== "object" ||
      !("bridge_version" in descriptor) ||
      descriptor.bridge_version !== 1 ||
      !("url" in descriptor) ||
      typeof descriptor.url !== "string" ||
      !("sha256" in descriptor) ||
      typeof descriptor.sha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(descriptor.sha256) ||
      !/^https:\/\/github.com\/langchain-ai\/open-swe\/releases\/download\/desktop-ui-[a-f0-9]{40}\/ui.json.gz$/.test(
        descriptor.url,
      )
    )
      throw new Error("Incompatible desktop UI descriptor");
    const archiveResponse = await this.fetchPublic(descriptor.url);
    const archive = new Uint8Array(await archiveResponse.arrayBuffer());
    if (archive.length > 100_000_000 || digest(archive) !== descriptor.sha256)
      throw new Error("Desktop UI archive checksum mismatch");
    const value: unknown = JSON.parse(
      gunzipSync(archive, { maxOutputLength: 300_000_000 }).toString("utf8"),
    );
    const snapshot = parseSnapshot(value);
    if (
      !value ||
      typeof value !== "object" ||
      !("files" in value) ||
      !Array.isArray(value.files)
    )
      throw new Error("Invalid desktop UI archive");
    await mkdir(this.directory, { recursive: true });
    for (let index = 0; index < snapshot.files.length; index++) {
      const file = snapshot.files[index];
      const entry: unknown = value.files[index];
      if (
        !entry ||
        typeof entry !== "object" ||
        !("body" in entry) ||
        typeof entry.body !== "string"
      )
        throw new Error("Missing desktop UI asset");
      const body = Buffer.from(entry.body, "base64");
      if (digest(body) !== file.sha256)
        throw new Error(`UI asset checksum mismatch: ${file.path}`);
      const target = path.join(this.directory, file.sha256);
      await writeFile(`${target}.tmp`, body);
      await rename(`${target}.tmp`, target);
    }
    const target = path.join(this.directory, "current.json");
    await writeFile(`${target}.tmp`, JSON.stringify(snapshot));
    await rename(`${target}.tmp`, target);
    return snapshot;
  }

  private async fetchPublic(url: string, redirects = 0): Promise<Response> {
    const target = new URL(url);
    if (
      target.protocol !== "https:" ||
      target.username ||
      target.password ||
      !["github.com", "release-assets.githubusercontent.com"].includes(
        target.hostname,
      ) ||
      redirects > 5
    )
      throw new Error("Untrusted desktop UI download URL");
    const response = await this.fetchUi(url, {
      method: "GET",
      redirect: "manual",
      credentials: "omit",
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
    if (response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location");
      await response.body?.cancel();
      if (!location) throw new Error("Missing UI download redirect");
      return this.fetchPublic(new URL(location, url).toString(), redirects + 1);
    }
    if (!response.ok)
      throw new Error(`UI download failed (${response.status})`);
    return response;
  }

  private async fetchHosted(pathname: string): Promise<Response> {
    const response = await this.fetchUi(
      backendRequestUrl(this.backendUrl, `${APP_URL}${pathname.slice(1)}`),
      {
        method: "GET",
        redirect: "manual",
        credentials: "omit",
        cache: "no-store",
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location");
      await response.body?.cancel();
      if (!location) throw new Error("Missing UI descriptor redirect");
      return this.fetchPublic(location);
    }
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error(`Dashboard UI unavailable (${response.status})`);
    }
    return response;
  }
}

module.exports = { DesktopUi };
