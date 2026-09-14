import { installDatadogPerfSink } from "./datadogSink"
import { exposePerfGlobal } from "./trace"

/** Client entry: sinks and the console handle. Spans themselves need no setup. */
export function initializePerf(): void {
  if (typeof window === "undefined") return
  exposePerfGlobal()
  installDatadogPerfSink()
}
