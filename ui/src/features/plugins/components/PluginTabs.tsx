import { useMatch } from "@tanstack/react-router"
import { useState } from "react"

import { PluginPage } from "@/features/plugins/components/PluginPage"
import { cn } from "@/lib/utils"

/**
 * Every plugin opened in this layout, kept mounted like browser tabs: the one
 * the route names is on screen, the rest wait hidden exactly as they were left,
 * each at its own path. The plugin route itself renders nothing.
 */
export function PluginTabs() {
  const match = useMatch({
    from: "/agents/plugins/$name/$",
    shouldThrow: false,
  })
  const active = match?.params.name
  const path = `/${match?.params._splat ?? ""}`
  const [opened, setOpened] = useState<ReadonlyArray<string>>([])
  const [paths, setPaths] = useState<Readonly<Record<string, string>>>({})
  if (active && !opened.includes(active)) setOpened([...opened, active])
  if (active && paths[active] !== path) setPaths({ ...paths, [active]: path })

  return opened.map((name) => (
    <div
      key={name}
      className={cn("flex min-h-0 min-w-0 flex-1", name !== active && "hidden")}
    >
      <PluginPage
        name={name}
        path={name === active ? path : (paths[name] ?? "/")}
        active={name === active}
        onPath={(next) =>
          setPaths((prev) =>
            prev[name] === next ? prev : { ...prev, [name]: next }
          )
        }
      />
    </div>
  ))
}
