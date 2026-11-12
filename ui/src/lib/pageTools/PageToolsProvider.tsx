import { createContext, useContext, useState } from "react"
import type { ReactNode } from "react"

import { PageModelContext } from "./PageModelContext"

// The app's one model context. Tools register only in effects, so the server
// render never adds any to it.
const PageToolsContext = createContext(new PageModelContext())

/**
 * Gives the tree below its own model context, apart from the app's: for a
 * test that calls tools directly, or a surface whose tools should not mix
 * with the page's.
 */
export function PageToolsProvider({
  context,
  children,
}: {
  context?: PageModelContext
  children: ReactNode
}) {
  const [owned] = useState(() => context ?? new PageModelContext())
  return (
    <PageToolsContext.Provider value={owned}>
      {children}
    </PageToolsContext.Provider>
  )
}

/** The model context page tools register on, for the code that offers and runs them. */
export function usePageModelContext(): PageModelContext {
  return useContext(PageToolsContext)
}
