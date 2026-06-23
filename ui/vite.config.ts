import http from "node:http"
import { defineConfig, type Plugin } from "vite"
import { devtools } from "@tanstack/devtools-vite"
import { tanstackStart } from "@tanstack/react-start/plugin/vite"
import viteReact from "@vitejs/plugin-react"
import viteTsConfigPaths from "vite-tsconfig-paths"
import tailwindcss from "@tailwindcss/vite"
import { nitro } from "nitro/vite"
import { VitePWA } from "vite-plugin-pwa"

// Dev-only: when E2E_HARNESS is set (the `dev:mock` local harness) serve the app
// and the harness from one origin by proxying the API routes + the Yjs collab
// WebSocket to the harness. Same-origin keeps the session cookie on the WS, which
// the plan-review collab requires. Inert in production (E2E_HARNESS unset).
function mockHarnessProxy(): Plugin | null {
  const target = process.env.E2E_HARNESS
  if (!target) return null
  const prefixes = [
    "/dashboard/api",
    "/webhooks",
    "/mock",
    "/control",
    "/fake-gh",
    "/fake-slack",
    "/static",
    "/ok",
  ]
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
          if (proxyHead?.length) socket.write(proxyHead)
          if (head?.length) proxySocket.write(head)
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

const config = defineConfig({
  optimizeDeps: { include: ["workbox-window"] },
  worker: { format: "es" },
  plugins: [
    mockHarnessProxy(),
    devtools(),
    nitro(),
    viteTsConfigPaths({
      projects: ["./tsconfig.json"],
    }),
    tailwindcss(),
    tanstackStart({ spa: { enabled: true } }),
    VitePWA({
      injectRegister: false,
      registerType: "prompt",
      outDir: ".output/public",
      devOptions: {
        enabled: true,
      },
      integration: {
        closeBundleOrder: "pre",
      },
      manifest: {
        id: "/",
        name: "Open SWE",
        short_name: "Open SWE",
        description: "Open-source coding agents for Slack, Linear, and GitHub.",
        start_url: "/",
        scope: "/",
        display: "standalone",
        theme_color: "#000000",
        background_color: "#ffffff",
        icons: [
          {
            src: "/favicon.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "/logo512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any maskable",
          },
        ],
      },
      workbox: {
        navigateFallback: "/_shell.html",
        navigateFallbackDenylist: [/^\/dashboard\/api\//, /^\/_serverFn\//],
        globPatterns: ["**/*.{js,css,png,svg,ico,webmanifest}"],
        additionalManifestEntries: [
          { url: "/_shell.html", revision: new Date().toISOString() },
        ],
      },
    }),
    viteReact(),
  ],
})

export default config
