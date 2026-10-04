import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import { normalizeBuildInfo } from "@/lib/api"
import { sessionQueryOptions } from "@/lib/session"

import { LiveTab } from "./client"
import type { LiveHello } from "./client"

/** Keeps every query that declares `meta.live` current while someone is signed in. */
export function LiveEvents() {
  const queryClient = useQueryClient()
  const { data: user } = useQuery(sessionQueryOptions)
  const signedIn = Boolean(user)

  useEffect(() => {
    if (!signedIn) return
    // A deploy drops every stream, so the first hello after it names the new
    // backend; the session query is what the version banner compares against.
    const onHello = (hello: LiveHello) => {
      const session = queryClient.getQueryData(sessionQueryOptions.queryKey)
      const deployed = normalizeBuildInfo(session?.build_info)?.backend.commit
      if (
        hello.backend_commit &&
        deployed &&
        deployed !== hello.backend_commit
      ) {
        void queryClient.invalidateQueries({
          queryKey: sessionQueryOptions.queryKey,
        })
      }
    }
    const tab = new LiveTab(queryClient, onHello)
    tab.start()
    return () => tab.stop()
  }, [queryClient, signedIn])

  return null
}
