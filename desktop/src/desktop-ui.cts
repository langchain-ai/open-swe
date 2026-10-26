const { backendRequestUrl, APP_URL } = require("./config.cjs");
const { createHash } = require("node:crypto");
const {
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rename,
  rm,
  writeFile,
} = require("node:fs/promises");
const path = require("node:path");
const { gunzipSync } = require("node:zlib");

type UiFetch = (url: string, init: RequestInit) => Promise<Response>;
type UiSnapshot = { version: string; files: string[] };

function digest(body: Uint8Array | string): string {
  return createHash("sha256").update(body).digest("hex");
}

function validAsset(pathname: string): boolean {
  return (
    /^\/(?:assets\/)?[a-zA-Z0-9_.-]+(?:\/[a-zA-Z0-9_.-]+)*$/.test(pathname) &&
    (pathname.startsWith("/assets/") || !pathname.slice(1).includes("/")) &&
    !pathname.includes("..")
  );
}

// fetch rejects with a TypeError, or a Timeout/Abort DOMException, when the
// host is unreachable; every other failure is the backend or release answering
// with something this app cannot use.
class IncompatibleUiError extends Error {}

function firstLoadFailure(error: unknown): string {
  if (error instanceof IncompatibleUiError)
    return "This version of Open SWE cannot run the UI your backend selected. Update the Open SWE desktop app, then use View → Reload.";
  const unreachable =
    error instanceof TypeError ||
    (error instanceof Error &&
      ["TimeoutError", "AbortError"].includes(error.name));
  return unreachable
    ? "Connect to the internet to download the Open SWE UI, then use View → Reload. " +
        "Check Open SWE → Backend URL… if the server is unavailable."
    : `Could not load the Open SWE UI: ${error instanceof Error ? error.message : String(error)}. ` +
        "The backend may not have published its desktop UI yet, or it needs a newer Open SWE desktop app.";
}

class DesktopUi {
  private snapshot: UiSnapshot | null = null;
  private update: Promise<UiSnapshot> | null = null;
  private warnedIncompatible = false;
  private readonly directory: string;

  constructor(
    readonly backendUrl: string,
    private readonly fetchUi: UiFetch,
    cacheRoot: string,
    private readonly serveFile: (filename: string) => Promise<Response>,
    private readonly localArchive: string | null = null,
    private readonly onIncompatible: (error: Error) => void = () => {},
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
      if (this.localArchive) {
        this.snapshot = await update;
      } else if (!this.snapshot) {
        try {
          this.snapshot = await update;
        } catch (error) {
          throw new Error(firstLoadFailure(error), { cause: error });
        }
      } else {
        void update.catch((error: unknown) => {
          if (
            error instanceof IncompatibleUiError &&
            !this.warnedIncompatible
          ) {
            this.warnedIncompatible = true;
            this.onIncompatible(error);
          }
          console.warn(
            "Could not update desktop UI; keeping cached version",
            error,
          );
        });
      }
    }
    if (!this.snapshot)
      return new Response("UI has not been downloaded", { status: 503 });
    const pathname = navigation
      ? "/_shell.html"
      : new URL(request.url).pathname;
    if (!this.snapshot.files.includes(pathname))
      return new Response("Not found", { status: 404 });
    return this.serveFile(
      path.join(this.directory, this.snapshot.version, pathname.slice(1)),
    );
  }

  private async readSnapshot(): Promise<UiSnapshot | null> {
    try {
      const value: unknown = JSON.parse(
        await readFile(path.join(this.directory, "current.json"), "utf8"),
      );
      if (
        !value ||
        typeof value !== "object" ||
        !("version" in value) ||
        typeof value.version !== "string" ||
        !/^[a-f0-9]{64}$/.test(value.version) ||
        !("files" in value) ||
        !Array.isArray(value.files) ||
        !value.files.every(
          (file: unknown) => typeof file === "string" && validAsset(file),
        ) ||
        !value.files.includes("/_shell.html")
      )
        throw new Error("Invalid cached UI snapshot");
      return { version: value.version, files: value.files };
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
    if (this.localArchive) {
      const archive = new Uint8Array(await readFile(this.localArchive));
      return this.install(archive, digest(archive));
    }
    const response = await this.fetchHosted("/desktop-ui.json");
    const descriptor: unknown = await response.json();
    if (
      descriptor &&
      typeof descriptor === "object" &&
      "bridge_version" in descriptor &&
      descriptor.bridge_version !== 1
    )
      throw new IncompatibleUiError(
        `Unsupported desktop UI bridge version ${String(descriptor.bridge_version)}`,
      );
    if (
      !descriptor ||
      typeof descriptor !== "object" ||
      !("bridge_version" in descriptor) ||
      !("url" in descriptor) ||
      typeof descriptor.url !== "string" ||
      !("sha256" in descriptor) ||
      typeof descriptor.sha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(descriptor.sha256) ||
      !/^https:\/\/github.com\/langchain-ai\/open-swe\/releases\/download\/desktop-ui-[a-f0-9]{40}\/ui.json.gz$/.test(
        descriptor.url,
      )
    )
      throw new Error("Invalid desktop UI descriptor");
    const current = await this.readSnapshot();
    if (current?.version === descriptor.sha256) return current;
    const archiveResponse = await this.fetchPublic(descriptor.url);
    const archive = new Uint8Array(await archiveResponse.arrayBuffer());
    if (digest(archive) !== descriptor.sha256)
      throw new Error("Desktop UI archive checksum mismatch");
    return this.install(archive, descriptor.sha256);
  }

  private async install(
    archive: Uint8Array,
    version: string,
  ): Promise<UiSnapshot> {
    if (archive.length > 100_000_000)
      throw new Error("Desktop UI archive is too large");
    const current = await this.readSnapshot();
    if (current?.version === version) return current;
    const value: unknown = JSON.parse(
      gunzipSync(archive, { maxOutputLength: 300_000_000 }).toString("utf8"),
    );
    if (
      !value ||
      typeof value !== "object" ||
      !("files" in value) ||
      !Array.isArray(value.files)
    )
      throw new Error("Invalid desktop UI archive");
    const files = value.files.map((entry: unknown) => {
      if (
        !entry ||
        typeof entry !== "object" ||
        !("path" in entry) ||
        typeof entry.path !== "string" ||
        !validAsset(entry.path) ||
        !("body" in entry) ||
        typeof entry.body !== "string"
      )
        throw new Error("Invalid desktop UI asset");
      return { path: entry.path, body: entry.body };
    });
    if (
      !files.some((file) => file.path === "/_shell.html") ||
      new Set(files.map((file) => file.path)).size !== files.length
    )
      throw new Error("Missing shell or duplicate desktop UI assets");
    const snapshot: UiSnapshot = {
      version,
      files: files.map((file) => file.path),
    };
    await mkdir(this.directory, { recursive: true });
    const staging: string = await mkdtemp(
      path.join(this.directory, "staging-"),
    );
    try {
      for (const file of files) {
        const target = path.join(staging, file.path.slice(1));
        await mkdir(path.dirname(target), { recursive: true });
        await writeFile(target, Buffer.from(file.body, "base64"));
      }
      try {
        await rename(staging, path.join(this.directory, snapshot.version));
      } catch (error) {
        if (
          !(
            error instanceof Error &&
            "code" in error &&
            ["EEXIST", "ENOTEMPTY"].includes(String(error.code))
          )
        )
          throw error;
      }
    } finally {
      await rm(staging, { recursive: true, force: true });
    }
    const target = path.join(this.directory, "current.json");
    await writeFile(`${target}.tmp`, JSON.stringify(snapshot));
    await rename(`${target}.tmp`, target);
    await this.prune(snapshot.version);
    return snapshot;
  }

  // The version being served keeps its directory so its lazy chunks still load.
  private async prune(current: string): Promise<void> {
    const keep = new Set([current, this.snapshot?.version, "current.json"]);
    for (const entry of await readdir(this.directory)) {
      if (keep.has(entry)) continue;
      await rm(path.join(this.directory, entry), {
        recursive: true,
        force: true,
      });
    }
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
