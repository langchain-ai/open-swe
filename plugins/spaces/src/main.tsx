import { connect } from "@openswe/plugin-sdk"
import { createRoot } from "react-dom/client"

import { App } from "./App"
import { HostProvider } from "./host"
import "./styles.css"

const host = await connect()
document.documentElement.dataset.theme = host.context.theme
host.on((event) => {
  if (event.type === "theme")
    document.documentElement.dataset.theme = event.theme
})

createRoot(document.getElementById("root")!).render(
  <HostProvider host={host}>
    <App />
  </HostProvider>
)
