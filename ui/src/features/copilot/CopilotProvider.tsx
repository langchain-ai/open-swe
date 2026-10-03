import { createContext, useContext, useMemo, useState } from "react"
import type { Dispatch, ReactNode, SetStateAction } from "react"
import { CopilotKitProvider } from "@copilotkit/react-core/v2"
import { COPILOT_RUNTIME_PATH } from "@/lib/copilotRuntimePath"
import { toolRenderers } from "./tools"

/** The composer's run settings; the backend reads them as the run's `configurable`. */
export interface RunConfigurable {
  agent_model_id?: string
  agent_effort?: string
  model_selection?: "auto" | "explicit"
  model_selection_changed?: boolean
  repo?: string | null
  repo_explicitly_none?: boolean
  environment?: string
}

interface RunSettings {
  configurable: RunConfigurable | null
  setConfigurable: Dispatch<SetStateAction<RunConfigurable | null>>
}

const RunSettingsContext = createContext<RunSettings | null>(null)

export function useRunSettings(): RunSettings {
  const settings = useContext(RunSettingsContext)
  if (!settings) throw new Error("useRunSettings needs a CopilotProvider")
  return settings
}

export function CopilotProvider({ children }: { children: ReactNode }) {
  const [configurable, setConfigurable] = useState<RunConfigurable | null>(null)
  const settings = useMemo(
    () => ({ configurable, setConfigurable }),
    [configurable]
  )
  const properties = useMemo(
    () => ({ configurable: configurable ?? {} }),
    [configurable]
  )
  return (
    <RunSettingsContext.Provider value={settings}>
      <CopilotKitProvider
        runtimeUrl={COPILOT_RUNTIME_PATH}
        properties={properties}
        renderToolCalls={toolRenderers}
        enableInspector={false}
        showIntelligenceIndicator={false}
      >
        {children}
      </CopilotKitProvider>
    </RunSettingsContext.Provider>
  )
}
