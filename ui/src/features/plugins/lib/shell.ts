/**
 * What a plugin runs in: its own HTML behind a policy that blocks its
 * requests, inside a shell frame the web UI writes. What a plugin sends goes
 * through the port the web UI hands it, over the web UI's own copy of
 * Cap'n Web, as requests the backend checks.
 *
 * The shell is there because no policy on a document stops that document
 * navigating its own frame elsewhere; its parent's `frame-src` does. The
 * shell's `frame-src 'none'` refuses every navigation the plugin's frame
 * attempts before a request leaves, in every browser, and the shell passes
 * the port to the plugin only after the plugin's document says hello.
 */
import {
  CONNECT_MESSAGE,
  HELLO_MESSAGE,
  NONCE_META,
  PROTOCOL_VERSION,
  RUNTIME_GLOBAL,
  type Theme,
} from "@openswe/plugin-sdk"
// oxlint-disable-next-line import/default -- Vite's ?raw import is the file's text
import CAPNWEB_MODULE from "capnweb?raw"

/**
 * An ES module with no imports, its exports gathered in one trailing
 * `export { … }`, as a classic script that leaves those exports at
 * `globalThis[global]`. Classic, so it runs before the plugin's own scripts,
 * of either kind, without needing a `data:` import the policy refuses.
 */
export function classicScript(module: string, global: string): string {
  const tail =
    /export\s*\{([^}]*)\};?\s*(?:\/\/# sourceMappingURL=\S*\s*)?$/.exec(module)
  const body = tail ? module.slice(0, tail.index) : ""
  if (!tail || /^\s*(?:import|export)\b/m.test(body))
    throw new Error("Cap'n Web is no longer one self-contained module")
  const exports = (tail[1] ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => {
      const [local, name = local] = entry.split(/\s+as\s+/)
      return `${JSON.stringify(name)}: ${local}`
    })
  return (
    `(() => {"use strict";\n${body}\n` +
    `Object.defineProperty(globalThis, ${JSON.stringify(global)}, ` +
    `{ value: Object.freeze({ ${exports.join(", ")} }) })\n})()`
  ).replaceAll("</script", "<\\/script")
}

/** The web UI's Cap'n Web, run in every plugin's document ahead of its own code. */
export const CAPNWEB_RUNTIME = classicScript(CAPNWEB_MODULE, RUNTIME_GLOBAL)

export const PLUGIN_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  "font-src data:",
  "img-src data: blob:",
  "media-src data: blob:",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-src 'none'",
  "worker-src 'none'",
  "connect-src 'none'",
].join("; ")

/**
 * No `allow-same-origin`: the frame gets an opaque origin, no cookies, and no
 * way into the page. `allow-forms` only lets forms fire `submit` for the
 * plugin's own handlers; `form-action 'none'` still blocks every submission.
 */
export const PLUGIN_SANDBOX = "allow-scripts allow-forms"
export const PLUGIN_ALLOW = "clipboard-write"

/** What the shell sends the dashboard when the plugin's frame loaded a second document. */
export const ESCAPED_MESSAGE = "openswe-plugin-escaped"

/**
 * The shell may run only its own script and frame nothing but the plugin's
 * srcdoc. That srcdoc inherits this policy on top of its own, so the shell
 * also allows the inline pictures, fonts and media the plugin's policy does.
 */
const SHELL_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  "font-src data:",
  "img-src data: blob:",
  "media-src data: blob:",
  "frame-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ")

function attribute(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;")
}

export function pluginDocument(
  html: string,
  theme: Theme,
  nonce: string
): string {
  // A meta policy governs only what follows it, so it comes before every byte
  // of the plugin's markup; a script ahead of it would run with the network open.
  // The plugin's own <html> and <head> tags merge into these.
  return (
    `<!doctype html><html data-theme="${theme}"><head>` +
    `<meta http-equiv="Content-Security-Policy" content="${PLUGIN_CSP}">` +
    `<meta name="${NONCE_META}" content="${nonce}">` +
    `<style>:root{color-scheme:${theme}}</style>` +
    `<script>${CAPNWEB_RUNTIME}</script>` +
    html.replace(/^\s*<!doctype[^>]*>/i, "")
  )
}

/**
 * The shell around a plugin: one full-size frame holding `pluginDocument`,
 * and a relay. It forwards the plugin's hello to the dashboard, hands the
 * port the dashboard answers with to the plugin, once, and reports the
 * plugin's frame if it ever loads a second document.
 */
export function pluginShell(html: string, theme: Theme, nonce: string): string {
  const relay = `(() => {
    const nonce = ${JSON.stringify(nonce)};
    const frame = document.getElementById("plugin");
    let loads = 0, asked = false, handed = false;
    frame.addEventListener("load", () => {
      if (++loads < 2) return;
      frame.remove();
      parent.postMessage({ type: ${JSON.stringify(ESCAPED_MESSAGE)}, nonce }, "*");
    });
    addEventListener("message", (event) => {
      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (event.source === frame.contentWindow && data.type === ${JSON.stringify(HELLO_MESSAGE)}
          && data.nonce === nonce && !asked && loads < 2) {
        asked = true;
        parent.postMessage({ type: ${JSON.stringify(HELLO_MESSAGE)}, nonce, version: ${PROTOCOL_VERSION} }, "*");
      } else if (event.source === parent && data.type === ${JSON.stringify(CONNECT_MESSAGE)}
          && event.ports[0] && asked && !handed && loads < 2) {
        handed = true;
        frame.contentWindow.postMessage(data, "*", [event.ports[0]]);
      }
    });
  })()`
  return (
    `<!doctype html><html><head>` +
    `<meta http-equiv="Content-Security-Policy" content="${SHELL_CSP}">` +
    `<style>:root{color-scheme:${theme}}html,body{margin:0;height:100%;overflow:hidden}` +
    `iframe{display:block;width:100%;height:100%;border:0}</style></head><body>` +
    `<iframe id="plugin" title="plugin" sandbox="${PLUGIN_SANDBOX}" allow="${PLUGIN_ALLOW}" ` +
    `referrerpolicy="no-referrer" srcdoc="${attribute(pluginDocument(html, theme, nonce))}"></iframe>` +
    `<script>${relay}</script></body></html>`
  )
}
