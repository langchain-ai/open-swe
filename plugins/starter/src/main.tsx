import { connect, type PluginHost } from "@openswe/plugin-sdk"
import { PluginHostProvider } from "@openswe/plugin-sdk/react"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { StrictMode, useEffect, useState } from "react"
import { createRoot } from "react-dom/client"

import { App } from "./App"
import "./styles.css"

// Listen as the module loads: the web UI hands over its port on the frame's
// load event, before React has mounted anything.
const pending = connect()

function Root() {
  const [host, setHost] = useState<PluginHost | null>(null)
  useEffect(() => {
    void pending.then(setHost)
  }, [])
  if (!host)
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    )
  return (
    <PluginHostProvider host={host}>
      <App />
    </PluginHostProvider>
  )
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>
)
