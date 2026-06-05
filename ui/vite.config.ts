import { defineConfig } from "vite"
import { devtools } from "@tanstack/devtools-vite"
import { tanstackStart } from "@tanstack/react-start/plugin/vite"
import viteReact from "@vitejs/plugin-react"
import viteTsConfigPaths from "vite-tsconfig-paths"
import tailwindcss from "@tailwindcss/vite"
import { nitro } from "nitro/vite"
import { VitePWA } from "vite-plugin-pwa"

const config = defineConfig({
  plugins: [
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
