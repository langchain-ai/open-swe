const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");
const { Readable } = require("node:stream");
const { APP_ORIGIN, isAppUrl } = require("./config.cjs");
const {
  resolveWorkspacePath,
  workspaceRelative,
} = require("./workspace-files.cjs");

export const PREVIEW_SCHEME = "open-swe-preview";
const PREVIEW_ORIGIN = `${PREVIEW_SCHEME}://files`;
const MAX_BYTES = 25 * 1024 * 1024;
const TTL_MS = 60 * 60 * 1000;
const RAW_ROUTE = /^\/dashboard\/api\/threads\/[^/]+\/files\/raw\/[^/]+\/./;
// Chromium's built-in PDF viewer, which renders a PDF preview in a frame of its own.
const PDF_VIEWER = "chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/";

// Same answers as the backend's raw route (openswe/threads/file_preview.py); change both together.
// Never "allow-same-origin", "allow-top-navigation*", "allow-popups*" or "allow-forms":
// any of them lets agent-written HTML act as the viewer or leave its frame.
const HTML_CSP = [
  "sandbox allow-scripts allow-downloads",
  "default-src 'none'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' https:",
  "style-src 'self' 'unsafe-inline' https:",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data: https:",
  "media-src 'self' data: blob: https:",
  "connect-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
  "frame-src 'none'",
  "worker-src 'none'",
].join("; ");
const SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox";
// An HTML page or Markdown document needs its folder: what it links relatively.
const FOLDER_EXTENSIONS = new Set([".html", ".htm", ".md", ".markdown"]);
const DEFAULT_CSP = "default-src 'none'; sandbox";
const INLINE_TYPES: Readonly<Record<string, string>> = {
  ".html": "text/html; charset=utf-8",
  ".htm": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json",
  ".map": "application/json",
  ".md": "text/plain; charset=utf-8",
  ".markdown": "text/plain; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".csv": "text/plain; charset=utf-8",
  ".tsv": "text/plain; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".avif": "image/avif",
  ".bmp": "image/bmp",
  ".ico": "image/x-icon",
  ".svg": "image/svg+xml",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".mov": "video/quicktime",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".ogg": "audio/ogg",
  ".m4a": "audio/mp4",
  ".pdf": "application/pdf",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".otf": "font/otf",
};
// What a sandboxed page can load from its folder. A page can read its own link and
// send it out, so data files are never in a folder's reach; they need a link of their own.
const FOLDER_ASSETS = new Set([
  ".html",
  ".htm",
  ".css",
  ".js",
  ".mjs",
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".avif",
  ".bmp",
  ".ico",
  ".svg",
  ".woff",
  ".woff2",
  ".ttf",
  ".otf",
  ".mp4",
  ".webm",
  ".mov",
  ".mp3",
  ".wav",
  ".ogg",
  ".m4a",
]);
const ALWAYS = {
  "X-Content-Type-Options": "nosniff",
  "Cache-Control": "private, no-store",
  "Referrer-Policy": "no-referrer",
  "Cross-Origin-Resource-Policy": "cross-origin",
  "Access-Control-Allow-Origin": "*",
};

/** Who may read which local file, or which HTML page's folder, until `exp`. */
type PreviewGrant = {
  session: string;
  root: string;
  kind: "file" | "directory";
  /** The file, or the page's folder, with symlinks resolved, relative to `root`. */
  path: string;
  exp: number;
  /** Every link is new, so a refresh in the same instant still loads afresh. */
  nonce: string;
};

type PreviewLink = { url: string };

/** How the backend proxy treats a request from the app's window. */
type ProxyCredentials =
  | "anonymous"
  | "session"
  | "forbidden"
  | "method-not-allowed";

class PreviewError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly headers: Record<string, string> = {},
  ) {
    super(message);
  }
}

/**
 * Local session files behind short-lived `open-swe-preview://` links, the
 * desktop's half of the backend's raw route. Tokens are signed with a key that
 * lives only in this process, so every link dies when the app quits.
 */
export class PreviewFiles {
  private readonly key = crypto.randomBytes(32);

  constructor(
    private readonly workspaceRoot: (sessionId: string) => string,
    private readonly now: () => number = Date.now,
  ) {}

  /** Whether `url` is a preview a confined frame may go on to show. */
  static isFrameUrl(url: string): boolean {
    if (
      url.split("#")[0] === "about:srcdoc" ||
      url.startsWith(`${PREVIEW_SCHEME}:`) ||
      url.startsWith(PDF_VIEWER)
    )
      return true;
    return isAppUrl(url) && RAW_ROUTE.test(new URL(url).pathname);
  }

  /**
   * A sandboxed or preview frame, or anything inside one, may not leave for a page
   * of its own choosing: `protocol.handle` sees no Origin, so the backend proxy
   * can't tell that page's requests from the app's. `frames` runs from the
   * navigating frame up to the app; a sandboxed frame's origin is `"null"`.
   */
  static blocksNavigation(
    frames: readonly { url: string; origin: string }[],
    targetUrl: string,
    initiatedByApp: boolean,
  ): boolean {
    return (
      !initiatedByApp &&
      frames.some(
        (frame) =>
          frame.origin === "null" || PreviewFiles.isFrameUrl(frame.url),
      ) &&
      !PreviewFiles.isFrameUrl(targetUrl)
    );
  }

  /**
   * Raw-file requests carry a token instead of the session, and only ever read.
   * Anything else that changes data gets the session only when the app's own page
   * sent it: `initiatorOrigin`, which Electron sets and no page can, is
   * `open-swe://app` for the app and `"null"` for a sandboxed frame.
   */
  static proxyCredentials(
    method: string,
    pathname: string,
    initiatorOrigin: string | undefined,
  ): ProxyCredentials {
    if (RAW_ROUTE.test(pathname))
      return method === "GET" ? "anonymous" : "method-not-allowed";
    if (["GET", "HEAD", "OPTIONS"].includes(method)) return "session";
    return initiatorOrigin === APP_ORIGIN ? "session" : "forbidden";
  }

  async mint(sessionId: string, relativePath: unknown): Promise<PreviewLink> {
    if (typeof sessionId !== "string") throw new Error("Invalid session");
    const { root, target, relative } = await resolveWorkspacePath(
      this.workspaceRoot(sessionId),
      relativePath,
    );
    const stat = await fs.stat(target);
    if (!stat.isFile()) throw new Error("Not a file.");
    if (stat.size > MAX_BYTES) throw new Error("File is too large to preview.");
    const folder = FOLDER_EXTENSIONS.has(
      path.posix.extname(relative).toLowerCase(),
    );
    const directory = path.posix.dirname(relative);
    const exp = this.now() + TTL_MS;
    const token = this.sign({
      session: sessionId,
      root,
      kind: folder ? "directory" : "file",
      path: folder ? (directory === "." ? "" : directory) : relative,
      exp,
      nonce: crypto.randomUUID(),
    });
    const name = encodeURIComponent(path.posix.basename(relative));
    return { url: `${PREVIEW_ORIGIN}/${token}/${name}` };
  }

  async serve(request: Request): Promise<Response> {
    try {
      if (request.method !== "GET")
        throw new PreviewError(405, "Method not allowed");
      const url = new URL(request.url);
      const [, token, ...encoded] = url.pathname.split("/");
      const grant = this.verify(token);
      const root = await fs.realpath(this.workspaceRoot(grant.session));
      if (root !== grant.root) throw new PreviewError(404, "Path not found.");
      const segments = encoded.map((segment) => decodeURIComponent(segment));
      return await this.answer(request, grant, segments);
    } catch (error) {
      const status = error instanceof PreviewError ? error.status : 404;
      const extra = error instanceof PreviewError ? error.headers : {};
      return new Response(
        status === 404 || !(error instanceof Error)
          ? "Path not found."
          : error.message,
        {
          status,
          headers: {
            ...ALWAYS,
            ...extra,
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Security-Policy": DEFAULT_CSP,
          },
        },
      );
    }
  }

  private async answer(
    request: Request,
    grant: PreviewGrant,
    segments: string[],
  ): Promise<Response> {
    const absolute = await this.target(grant, segments);
    const handle = await fs.open(
      absolute,
      fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW,
    );
    let streaming = false;
    try {
      const stat = await handle.stat();
      if (!stat.isFile()) throw new PreviewError(404, "Path not found.");
      if (stat.size > MAX_BYTES)
        throw new PreviewError(413, "File is too large to preview.");
      const headers: Record<string, string> = {
        ...PreviewFiles.typeHeaders(absolute),
        "Accept-Ranges": "bytes",
      };
      const span = PreviewFiles.range(request.headers.get("range"), stat.size);
      headers["Content-Length"] = String(span.end - span.start + 1);
      if (span.partial)
        headers["Content-Range"] =
          `bytes ${span.start}-${span.end}/${stat.size}`;
      const status = span.partial ? 206 : 200;
      if (stat.size === 0) return new Response(null, { status, headers });
      const body = Readable.toWeb(
        handle.createReadStream({ start: span.start, end: span.end }),
      );
      streaming = true;
      return new Response(body, { status, headers });
    } finally {
      if (!streaming) await handle.close();
    }
  }

  /** The absolute file `segments` names under `grant`, symlinks resolved, or a 404. */
  private async target(
    grant: PreviewGrant,
    segments: string[],
  ): Promise<string> {
    const notFound = new PreviewError(404, "Path not found.");
    let requested: string;
    if (grant.kind === "file") {
      if (segments.join("/") !== path.posix.basename(grant.path))
        throw notFound;
      requested = grant.path;
    } else {
      const extension = path.posix
        .extname(segments[segments.length - 1] ?? "")
        .toLowerCase();
      if (
        segments.some(
          (segment) =>
            !segment || segment.startsWith(".") || /[/\\\0]/.test(segment),
        ) ||
        !FOLDER_ASSETS.has(extension)
      )
        throw notFound;
      requested = path.posix.join(grant.path, ...segments);
    }
    let absolute: string;
    try {
      absolute = await fs.realpath(path.join(grant.root, requested));
    } catch {
      throw notFound;
    }
    let relative: string;
    try {
      relative = workspaceRelative(grant.root, absolute);
    } catch {
      throw notFound;
    }
    if (grant.kind === "file" && relative !== grant.path) throw notFound;
    if (grant.kind === "directory") {
      // Still the folder the grant recorded: a symlink swapped in would carry it elsewhere.
      const folder = path.join(grant.root, grant.path);
      if ((await fs.realpath(folder)) !== folder) throw notFound;
      const below = path.relative(folder, absolute).split(path.sep);
      if (below[0] === ".." || below.some((part) => part.startsWith(".")))
        throw notFound;
    }
    return absolute;
  }

  private static typeHeaders(file: string): Record<string, string> {
    const extension = path.extname(file).toLowerCase();
    const mediaType = INLINE_TYPES[extension];
    const name =
      path
        .basename(file)
        .replace(/[^A-Za-z0-9._-]+/g, "_")
        .replace(/^[._-]+|[._-]+$/g, "")
        .slice(0, 100) || "file";
    const csp = !mediaType
      ? DEFAULT_CSP
      : mediaType.startsWith("text/html")
        ? HTML_CSP
        : mediaType === "image/svg+xml"
          ? SVG_CSP
          : DEFAULT_CSP;
    return {
      ...ALWAYS,
      "Content-Type": mediaType ?? "application/octet-stream",
      "Content-Security-Policy": csp,
      "Content-Disposition": `${mediaType ? "inline" : "attachment"}; filename="${name}"`,
    };
  }

  private static range(
    header: string | null,
    size: number,
  ): { start: number; end: number; partial: boolean } {
    const whole = { start: 0, end: size - 1, partial: false };
    const match = /^bytes=(\d*)-(\d*)$/.exec((header ?? "").trim());
    if (!match || (!match[1] && !match[2])) return whole;
    const [start, end] = match[1]
      ? [Number(match[1]), match[2] ? Number(match[2]) : size - 1]
      : [Math.max(size - Number(match[2]), 0), size - 1];
    if (start >= size || start > end)
      throw new PreviewError(416, "Requested range not satisfiable.", {
        "Content-Range": `bytes */${size}`,
      });
    return { start, end: Math.min(end, size - 1), partial: true };
  }

  private sign(grant: PreviewGrant): string {
    const payload = Buffer.from(JSON.stringify(grant)).toString("base64url");
    return `${payload}.${this.signature(payload)}`;
  }

  private verify(token: string | undefined): PreviewGrant {
    const expired = new PreviewError(
      401,
      "This preview link expired. Open the file again.",
    );
    const [payload, signature, extra] = (token ?? "").split(".");
    if (!payload || !signature || extra !== undefined) throw expired;
    const expected = Buffer.from(this.signature(payload));
    const actual = Buffer.from(signature);
    if (
      actual.length !== expected.length ||
      !crypto.timingSafeEqual(actual, expected)
    )
      throw expired;
    const grant: unknown = JSON.parse(
      Buffer.from(payload, "base64url").toString(),
    );
    if (!PreviewFiles.isGrant(grant) || grant.exp <= this.now()) throw expired;
    return grant;
  }

  private static isGrant(value: unknown): value is PreviewGrant {
    if (typeof value !== "object" || value === null) return false;
    const grant = value as Record<string, unknown>;
    return (
      typeof grant.session === "string" &&
      typeof grant.root === "string" &&
      (grant.kind === "file" || grant.kind === "directory") &&
      typeof grant.path === "string" &&
      typeof grant.exp === "number"
    );
  }

  private signature(payload: string): string {
    return crypto
      .createHmac("sha256", this.key)
      .update(payload)
      .digest("base64url");
  }
}
