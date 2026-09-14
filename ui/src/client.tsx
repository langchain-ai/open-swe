import { StartClient } from "@tanstack/react-start/client"
import { createRoot, hydrateRoot } from "react-dom/client"

import { initializeDatadogRum } from "./lib/datadog"
import { installDatadogPerfSink } from "./lib/perf/datadogSink"
import { exposePerfGlobal } from "./lib/perf/trace"

exposePerfGlobal()
installDatadogPerfSink()
void initializeDatadogRum()

const app = <StartClient />
if (window.openSweDesktop) createRoot(document).render(app)
else hydrateRoot(document, app)
