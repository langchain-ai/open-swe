import type { StoredDocument } from "@openswe/plugin-sdk"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react"

import { errorMessage, useHost } from "./host"
import { DRAFTS, isMarkdown, type Draft } from "./model"

export interface Loadable<T> {
  data: T | undefined
  error: string | null
  loading: boolean
  reload: () => void
}

/** `load`'s latest answer; it runs again whenever `deps` or `reload` change it. */
export function useLoad<T>(
  load: () => Promise<T>,
  deps: ReadonlyArray<unknown>
): Loadable<T> {
  const [state, setState] = useState<{ data?: T; error: string | null }>({
    error: null,
  })
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    let live = true
    setLoading(true)
    load()
      .then(
        (data) => live && setState({ data, error: null }),
        (error: unknown) =>
          live && setState((prev) => ({ ...prev, error: errorMessage(error) }))
      )
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `deps` names what `load` reads.
  }, [...deps, nonce])
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data: state.data, error: state.error, loading, reload }
}

export interface Space {
  repository: string
  defaultBranch: string
  pages: string[]
  truncated: boolean
  drafts: Map<string, Draft>
  /** Bumps whenever anyone writes a document in this repository's scope. */
  changes: number
  reloadPages: () => void
  reloadDrafts: () => void
}

const SpaceContext = createContext<Space | null>(null)

export function useSpace(): Space {
  const space = useContext(SpaceContext)
  if (!space) throw new Error("useSpace outside SpaceProvider")
  return space
}

export function SpaceProvider({
  repository,
  children,
  fallback,
}: {
  repository: string
  children: ReactNode
  fallback: (state: { error: string | null }) => ReactNode
}) {
  const host = useHost()
  const [changes, setChanges] = useState(0)
  useEffect(
    () => host.storage.watch({ repository }, () => setChanges((n) => n + 1)),
    [host, repository]
  )
  const tree = useLoad(async () => {
    const github = host.github(repository)
    const info = await github.info()
    const sha = await github.ref(info.defaultBranch)
    if (!sha) throw new Error(`${repository} has no ${info.defaultBranch}`)
    const listing = await github.tree(sha)
    return {
      defaultBranch: info.defaultBranch,
      truncated: listing.truncated,
      pages: listing.entries
        .filter((entry) => entry.type === "blob" && isMarkdown(entry.path))
        .map((entry) => entry.path)
        .sort((a, b) => a.localeCompare(b)),
    }
  }, [host, repository])
  const drafts = useLoad(
    async () =>
      new Map(
        (
          (await host.storage.query<Draft>(DRAFTS, {
            repository,
          })) as StoredDocument<Draft>[]
        ).map((draft) => [draft.key, draft.document])
      ),
    [host, repository, changes]
  )

  if (!tree.data || !drafts.data)
    return <>{fallback({ error: tree.error ?? drafts.error })}</>
  return (
    <SpaceContext
      value={{
        repository,
        defaultBranch: tree.data.defaultBranch,
        pages: tree.data.pages,
        truncated: tree.data.truncated,
        drafts: drafts.data,
        changes,
        reloadPages: tree.reload,
        reloadDrafts: drafts.reload,
      }}
    >
      {children}
    </SpaceContext>
  )
}
