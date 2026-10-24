import { StartClient } from "@tanstack/react-start/client"
import { createRoot, hydrateRoot } from "react-dom/client"

import { initializeDatadogRum } from "./lib/datadog"
import {
  installClientErrorReporting,
  recordClientError,
} from "./lib/clientErrors"
import { installDatadogPerfSink } from "./lib/perf/datadogSink"
import { exposePerfGlobal } from "./lib/perf/trace"

// This bundle's own identity, stamped by the build; never the backend's.
window.__OPEN_SWE_BUNDLE__ = {
  commit: __OPEN_SWE_BUNDLE_COMMIT__,
  built_at: __OPEN_SWE_BUNDLE_BUILT_AT__,
}

installClientErrorReporting()
exposePerfGlobal()
installDatadogPerfSink()
void initializeDatadogRum()

const app = <StartClient />
const errorOptions = {
  onCaughtError: (error: unknown) => {
    recordClientError({ title: "React render failed", error })
  },
  onUncaughtError: (error: unknown) => {
    recordClientError({ title: "React render failed", error })
  },
  onRecoverableError: (error: unknown) => {
    recordClientError({ title: "React recovered from an error", error })
  },
}
if (window.openSweDesktop) createRoot(document, errorOptions).render(app)
else hydrateRoot(document, app, errorOptions)
