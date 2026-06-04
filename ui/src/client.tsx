import { StartClient } from "@tanstack/react-start/client"
import { StrictMode } from "react"
import { hydrateRoot } from "react-dom/client"
import { registerSW } from "virtual:pwa-register"

// prompt mode: a new SW installs in the background and takes over on the next
// load, so deploys never reload a tab mid-agent-run.
registerSW()

hydrateRoot(
  document,
  <StrictMode>
    <StartClient />
  </StrictMode>
)
