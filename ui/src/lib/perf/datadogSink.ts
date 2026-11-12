/**
 * Forwards ended spans to Datadog RUM as custom duration vitals, so production
 * thread-load and streaming numbers land next to the sessions they came from
 * (RUM Explorer: `@type:vital @vital.name:thread_load`). Attributes ride in the
 * vital's context; thread and run ids stay out, matching the URL sanitizer.
 */

import {
  getDatadogRum,
  isDatadogRumInitialized,
  subscribeToDatadogInitialization,
} from "@/lib/datadog"
import { getPerfSpans, registerPerfSink, type PerfSink } from "./trace"

export function installDatadogPerfSink(): void {
  let installed = false
  const attach = () => {
    if (installed) return
    const rum = getDatadogRum()
    if (!rum?.addDurationVital) return
    installed = true
    rum.setGlobalContextProperty?.(
      "client",
      window.openSweDesktop ? "desktop" : "web"
    )
    const addDurationVital = rum.addDurationVital
    const send: PerfSink = (span) => {
      if (span.duration === null) return
      const context = { ...span.attributes }
      for (const step of span.steps)
        context[`step_${step.name}_ms`] = Math.round(step.at)
      addDurationVital(span.name, {
        startTime: span.startEpochMs,
        duration: span.duration,
        context,
      })
    }
    for (const span of getPerfSpans()) {
      if (span.status === "ended") send(span)
    }
    registerPerfSink(send)
  }
  if (isDatadogRumInitialized()) attach()
  else subscribeToDatadogInitialization(attach)
}
