import { connect, type PluginHost } from "@openswe/plugin-sdk"
import { PluginHostProvider } from "@openswe/plugin-sdk/react"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { StrictMode, useEffect, useState } from "react"
import { createRoot } from "react-dom/client"

import { App } from "./App"
import "./styles.css"

// Connect once, as the module loads: the web UI answers only the frame's first
// hello, so a second connect(), such as from an effect StrictMode runs twice,
// would never resolve.
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
