const { APP_ORIGIN, isAppUrl } = require("./config.cjs");

const RAW_ROUTE = /^\/dashboard\/api\/threads\/[^/]+\/files\/raw\/[^/]+\/./;
// Chromium's built-in PDF viewer, which renders a PDF preview in a frame of its own.
const PDF_VIEWER = "chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/";

/** How the backend proxy treats a request from the app's window. */
type ProxyCredentials =
  | "anonymous"
  | "session"
  | "forbidden"
  | "method-not-allowed";

/**
 * What the frames that show agent-written files may do: the backend's raw file
 * route serves them into sandboxed frames of the app's window, and those frames
 * must neither leave for a page of their own choosing nor act with the session.
 */
export class FramePolicy {
  /** Whether `url` is a preview a confined frame may go on to show. */
  static isFrameUrl(url: string): boolean {
    if (url.split("#")[0] === "about:srcdoc" || url.startsWith(PDF_VIEWER))
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
        (frame) => frame.origin === "null" || FramePolicy.isFrameUrl(frame.url),
      ) &&
      !FramePolicy.isFrameUrl(targetUrl)
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
}
