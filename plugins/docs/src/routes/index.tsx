import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { useHostContext } from "@openswe/plugin-sdk/react"
import { FileTextIcon } from "@phosphor-icons/react/dist/ssr/FileText"
import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/")({ component: Home })

function Home() {
  const { plugin } = useHostContext()
  const first = plugin.repositories[0]
  if (first) {
    const [owner = "", repo = ""] = first.split("/")
    return <Navigate to="/$owner/$repo" params={{ owner, repo }} replace />
  }
  return (
    <div className="flex h-svh items-center justify-center p-space-6">
      <EmptyState
        icon={FileTextIcon}
        title="Give Docs a repository"
        description="Docs keeps its pages in a GitHub repository. Add one to the plugin under Settings → Plugins, then come back."
      />
    </div>
  )
}
