import { StartClient } from "@tanstack/react-start/client"
import { StrictMode } from "react"
import { hydrateRoot } from "react-dom/client"
import { registerSW } from "virtual:pwa-register"

registerSW({ immediate: true })

hydrateRoot(
  document,
  <StrictMode>
    <StartClient />
  </StrictMode>
)
