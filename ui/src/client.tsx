import { StartClient } from "@tanstack/react-start/client"
import { createRoot, hydrateRoot } from "react-dom/client"

import { initializeDatadogRum } from "./lib/datadog"
import { installDatadogPerfSink } from "./lib/perf/datadogSink"
import { exposePerfGlobal } from "./lib/perf/trace"

// This bundle's own identity, stamped by the build; never the backend's.
window.__OPEN_SWE_BUNDLE__ = {
  commit: __OPEN_SWE_BUNDLE_COMMIT__,
  built_at: __OPEN_SWE_BUNDLE_BUILT_AT__,
}

exposePerfGlobal()
installDatadogPerfSink()
void initializeDatadogRum()

const app = <StartClient />
if (window.openSweDesktop) createRoot(document).render(app)
else hydrateRoot(document, app)
