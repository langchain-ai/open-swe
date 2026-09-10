import http from "node:http"
import { defineConfig } from "vite"
import { devtools } from "@tanstack/devtools-vite"
import { tanstackStart } from "@tanstack/react-start/plugin/vite"
import viteReact from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { nitro } from "nitro/vite"
import type { IncomingMessage } from "node:http"
import type { Plugin } from "vite"

// Paths the backend owns, not the app router. `/dashboard/api` is the only one a
// deployed dashboard serves; the rest exist when the backend is the mock harness,
// and a browser navigates to `/fake-gh` mid-login, so dev has to reach them too.
const BACKEND_PREFIXES = [
  "/dashboard/api",
  "/webhooks",
  "/mock",
  "/control",
  "/fake-gh",
  "/fake-slack",
  "/static",
  "/ok",
  // LangGraph's own API, which the harness serves alongside the fake SaaS. The
  // app router has no route under either, so fronting them shadows nothing.
  "/threads",
  "/store",
]

// The harness-owned subset: everything but the two a deployment fronts and
// `/static`, which would shadow nitro's own assets.
const E2E_HARNESS_PREFIXES = BACKEND_PREFIXES.filter(
  (prefix) => !["/dashboard/api", "/webhooks", "/static"].includes(prefix)
)

function matchesBackendPrefix(url?: string): boolean {
  return (
    !!url &&
    BACKEND_PREFIXES.some(
      (p) => url === p || url.startsWith(`${p}/`) || url.startsWith(`${p}?`)
    )
  )
}

// Dev-only: when E2E_HARNESS is set (the `dev:mock` local harness) serve the app
// and the harness from one origin by proxying the API routes + the Yjs collab
// WebSocket to the harness. Same-origin keeps the session cookie on the WS, which
// the plan-review collab requires. Inert in production (E2E_HARNESS unset).
function mockHarnessProxy(): Plugin | null {
  const target = process.env.E2E_HARNESS
  if (!target) return null
  const matches = matchesBackendPrefix
  const upstream = new URL(target)
  return {
    name: "mock-harness-proxy",
    enforce: "pre",
    async configureServer(server) {
      const { createProxyServer } = await import("httpxy")
      const proxy = createProxyServer({ target })
      proxy.on("error", () => {})
      server.middlewares.use((req, res, next) => {
        if (matches(req.url)) void proxy.web(req, res).catch(() => {})
        else next()
      })
      // Proxy the Yjs WebSocket by hand (httpxy's ws upgrade is unreliable here).
      // Only claim our paths; Vite's own HMR socket upgrade is left untouched.
      server.httpServer?.on("upgrade", (req, socket, head) => {
        if (!matches(req.url)) return
        const proxyReq = http.request({
          host: upstream.hostname,
          port: upstream.port,
          method: "GET",
          path: req.url,
          headers: req.headers,
        })
        proxyReq.on("upgrade", (proxyRes, proxySocket, proxyHead) => {
          const lines = Object.entries(proxyRes.headers)
            .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`)
            .join("\r\n")
          socket.write(
            `HTTP/1.1 ${proxyRes.statusCode} ${proxyRes.statusMessage}\r\n${lines}\r\n\r\n`
          )
          if (proxyHead.length) socket.write(proxyHead)
          if (head.length) proxySocket.write(head)
          proxySocket.on("error", () => socket.destroy())
          socket.on("error", () => proxySocket.destroy())
          proxySocket.pipe(socket)
          socket.pipe(proxySocket)
        })
        proxyReq.on("error", () => socket.destroy())
        proxyReq.end()
      })
    },
  }
}

// shiki lazily `import()`s one grammar per language on first render. These libs
// also only live inside lazy route components, so Vite's startup scanner never
// reaches them — it discovers them on first thread navigation, re-optimizes deps,
// and force-reloads, aborting the in-flight route-chunk import ("Failed to fetch
// dynamically imported module"). Pre-bundling them up front avoids the reload.
// Uncommon languages not listed just trigger a one-time, graceful re-optimize.
const SHIKI_LANGS = [
  "bash",
  "c",
  "cpp",
  "csharp",
  "css",
  "diff",
  "docker",
  "go",
  "graphql",
  "html",
  "java",
  "javascript",
  "json",
  "jsonc",
  "jsx",
  "kotlin",
  "lua",
  "make",
  "markdown",
  "php",
  "python",
  "ruby",
  "rust",
  "scala",
  "shellscript",
  "sql",
  "swift",
  "toml",
  "tsx",
  "typescript",
  "xml",
  "yaml",
]

// Browser `/dashboard/api/*` calls are proxied to the Python backend by the
// server rather than sent cross-origin, so the session cookie stays same-origin.
const IS_PRODUCTION = process.env.NODE_ENV === "production"

// Dev proxies every backend path in-process so a local mock backend's login
// redirects resolve on this origin. A deployed build proxies at runtime instead,
// in server/middleware/backend-proxy.ts, so one image can front any backend.
const devRouteRules = IS_PRODUCTION
  ? {}
  : Object.fromEntries(
      BACKEND_PREFIXES.map((prefix) => [
        `${prefix}/**`,
        {
          proxy: {
            to: `${process.env.DASHBOARD_API_URL ?? "http://localhost:2024"}${prefix}/**`,
            fetchOptions: { redirect: "manual" as const },
          },
        },
      ])
    )

// The nitro dev handler builds its request from `rawHeaders`, so setting only
// `headers` here would be dropped before anything is proxied.
function setRequestHeader(
  req: IncomingMessage,
  name: string,
  value: string
): void {
  req.headers[name] = value
  const raw = req.rawHeaders
  for (let i = raw.length - 2; i >= 0; i -= 2) {
    if (raw[i]?.toLowerCase() === name) raw.splice(i, 2)
  }
  raw.push(name, value)
}

// `pnpm run dev:prod`: a deployed backend keeps `osw_session` on its own origin and
// rejects mutations carrying any other Origin, so this dev server presents the
// session that target minted (scripts/dev-prod.mjs) and speaks as that origin.
// The cookie also has to reach the server render, which forwards the incoming
// one by hand; the Origin rewrite is confined to proxied paths so Vite's own
// module and HMR requests keep the origin they arrived with.
function deployedBackendSession(): Plugin | null {
  const session = process.env.OPEN_SWE_DEV_SESSION
  const backend = process.env.DASHBOARD_API_URL
  if (!session || !backend) return null
  const origin = new URL(backend).origin
  return {
    name: "deployed-backend-session",
    enforce: "pre",
    configureServer(server) {
      const ownOrigins = new Set([
        `http://localhost:${DEV_PORT}`,
        `http://127.0.0.1:${DEV_PORT}`,
      ])
      server.middlewares.use((req, res, next) => {
        if (matchesBackendPrefix(req.url)) {
          // Any page open in the browser can post here while this runs, and a
          // blanket rewrite would launder its Origin past the backend's CSRF
          // check on a session it never had. Only this server's pages get that.
          const from = req.headers.origin
          if (from && !ownOrigins.has(from)) {
            res.writeHead(403).end()
            return
          }
          if (from) setRequestHeader(req, "origin", origin)
          if (req.headers.referer)
            setRequestHeader(req, "referer", `${origin}/`)
        }
        const jar = (req.headers.cookie ?? "")
          .split(";")
          .map((cookie) => cookie.trim())
          .filter((cookie) => cookie && !cookie.startsWith("osw_session="))
        jar.push(`osw_session=${session}`)
        setRequestHeader(req, "cookie", jar.join("; "))
        next()
      })
    },
  }
}

// The Electron app and the service worker's offline navigation both load a
// client-only `_shell.html`. SSR alone doesn't emit one, so prerender `/` with
// the header that tells the Start handler to render the shell instead of the route.
const SHELL_PAGE = {
  path: "/",
  prerender: {
    enabled: true,
    outputPath: "/_shell",
    autoSubfolderIndex: false,
    crawlLinks: false,
    headers: { "X-TSS_SHELL": "true" },
  },
  sitemap: { exclude: true },
}

// Where the app is served from. "/" for the dev server and the standalone image;
// a LangGraph `http.mount_prefix` (plus trailing slash) when the backend bundles
// the build and serves it under that prefix.
const BASE_PATH = process.env.DASHBOARD_BASE_PATH || "/"

// The backend can front this dev server (DASHBOARD_DEV_SERVER_URL) so the UI
// hot-reloads on its origin. HTTP is proxied; the HMR WebSocket is not, so the
// client is told to open it against this port whatever page origin it loaded from.
const DEV_PORT = Number(process.env.PORT) || 3000

const config = defineConfig({
  base: BASE_PATH,
  server: { port: DEV_PORT, strictPort: true, hmr: { clientPort: DEV_PORT } },
  resolve: { tsconfigPaths: true },
  optimizeDeps: {
    include: [
      "streamdown",
      "shiki",
      "@pierre/diffs",
      "@pierre/diffs/react",
      "@pierre/trees",
      "@pierre/trees/react",
      "@shikijs/themes/github-light",
      "@shikijs/themes/github-dark",
      ...SHIKI_LANGS.map((lang) => `@shikijs/langs/${lang}`),
    ],
  },
  worker: { format: "es" },
  plugins: [
    deployedBackendSession(),
    mockHarnessProxy(),
    devtools(),
    nitro({
      routeRules: devRouteRules,
      // Registered explicitly: nitro's convention scan does not reach this
      // directory under the vite plugin. Deployed builds only — dev proxies the
      // same prefixes through devRouteRules, which has a localhost default the
      // handler deliberately refuses to have. Only the two prefixes a deployed
      // dashboard fronts, since proxying `/static` would shadow nitro's assets.
      handlers: IS_PRODUCTION
        ? [
            "/dashboard/api",
            "/webhooks",
            // A built server fronting the mock harness fronts its fake-SaaS and
            // control routes too, so the E2E browser has the one origin a
            // deployment gives it and reaches the backend the way it really
            // does — through the handler below. Serving the app from the
            // harness instead let the suite pass while this proxy was broken.
            ...(process.env.E2E_HARNESS ? E2E_HARNESS_PREFIXES : []),
          ].map((prefix) => ({
            route: `${prefix}/**`,
            handler: "./server/backend-proxy.ts",
          }))
        : [],
      // Nitro gives every node_modules package its own server chunk. The
      // LangGraph SDK reaches CJS-only `eventemitter3` through `p-queue`, and
      // splitting that cycle puts the CommonJS interop helper in the SDK's chunk
      // while eventemitter3's chunk calls it at module scope — one tick before
      // it exists. Rendering any route that imports the SDK then throws
      // `__commonJSMin is not a function` and falls back to the client. Keeping
      // the cycle in one chunk gives it one initialisation order.
      // One server chunk instead of one per package. The `@langchain/*` +
      // `langsmith` + `p-queue` + `eventemitter3` dependency cycle is CommonJS,
      // and splitting it across chunks leaves each chunk reading the other's
      // interop helper before it initialises (`__commonJSMin is not a function`,
      // `Cannot access 'PQueueMod' before initialization`). Those throw during
      // `renderToReadableStream`, so every route silently fell back to client
      // rendering. Nitro's chunk groups can't be overridden — its own catch-all
      // group is merged ahead of any user group — but disabling code splitting
      // gives the cycle a single initialisation order.
      inlineDynamicImports: true,
    }),
    tailwindcss(),
    tanstackStart({ pages: [SHELL_PAGE] }),
    // React Compiler via oxc-transform-react, the Rust port. Upstream still
    // marks it experimental; the fallback is `@rolldown/plugin-babel` with
    // plugin-react's `reactCompilerPreset()`, which runs the Babel compiler.
    viteReact({ compiler: true }),
  ],
})

export default config
