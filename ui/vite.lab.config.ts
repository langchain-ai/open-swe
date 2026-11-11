import { fileURLToPath } from "node:url"
import { defineConfig } from "vite"
import tailwindcss from "@tailwindcss/vite"
import viteReact from "@vitejs/plugin-react"

// Dev-only server for the Trace Flight bird lab (`pnpm --dir ui lab`). The
// production build runs vite.config.ts, which never reaches this page, so none
// of the lab ships. Keep it out of vite.config.ts and the route tree.
export default defineConfig({
  root: fileURLToPath(
    new URL("./src/features/trace-flight/hosts/lab", import.meta.url)
  ),
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  plugins: [tailwindcss(), viteReact({ compiler: true })],
  server: { port: 3100 },
})
