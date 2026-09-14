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
import { registerPerfSink } from "./trace"

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
    registerPerfSink((span) => {
      if (span.duration === null) return
      const context = { ...span.attributes }
      for (const step of span.steps)
        context[`step_${step.name}_ms`] = Math.round(step.at)
      addDurationVital(span.name, {
        startTime: span.startEpochMs,
        duration: span.duration,
        context,
      })
    })
  }
  if (isDatadogRumInitialized()) attach()
  else subscribeToDatadogInitialization(attach)
}
