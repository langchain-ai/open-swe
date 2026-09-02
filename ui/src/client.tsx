import { StartClient } from "@tanstack/react-start/client"
import { createRoot, hydrateRoot } from "react-dom/client"

import { initializeDatadogRum } from "./lib/datadog"

void initializeDatadogRum()

const app = <StartClient />
if (window.openSweDesktop) createRoot(document).render(app)
else hydrateRoot(document, app)
