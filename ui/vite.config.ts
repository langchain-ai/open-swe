import http from "node:http"
import { defineConfig } from "vite"
import { devtools } from "@tanstack/devtools-vite"
import { tanstackStart } from "@tanstack/react-start/plugin/vite"
import viteReact from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { nitro } from "nitro/vite"
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

// Dev-only: when E2E_HARNESS is set (the `dev:mock` local harness) serve the app
// and the harness from one origin by proxying the API routes + the Yjs collab
// WebSocket to the harness. Same-origin keeps the session cookie on the WS, which
// the plan-review collab requires. Inert in production (E2E_HARNESS unset).
function mockHarnessProxy(): Plugin | null {
  const target = process.env.E2E_HARNESS
  if (!target) return null
  const prefixes = BACKEND_PREFIXES
  const matches = (url?: string): boolean =>
    !!url &&
    prefixes.some(
      (p) => url === p || url.startsWith(`${p}/`) || url.startsWith(`${p}?`)
    )
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

const config = defineConfig({
  base: "/",
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
