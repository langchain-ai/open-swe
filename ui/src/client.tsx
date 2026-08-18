import { StartClient } from "@tanstack/react-start/client"
import { createRoot, hydrateRoot } from "react-dom/client"

const app = <StartClient />
if (window.openSweDesktop) createRoot(document).render(app)
else hydrateRoot(document, app)
