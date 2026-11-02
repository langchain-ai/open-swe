import type { Person, PluginHost } from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo } from "react"

import { commentsIn, draftsIn, isMarkdown, repositoryLabel } from "@/lib/model"

export const keys = {
  repositories: ["repositories"] as const,
  space: (repository: string) => ["space", repository] as const,
  drafts: (repository: string) => ["drafts", repository] as const,
  comments: (repository: string, path: string) =>
    ["comments", repository, path] as const,
  file: (repository: string, path: string, sha: string) =>
    ["file", repository, path, sha] as const,
  head: (repository: string, ref: string) => ["head", repository, ref] as const,
  history: (repository: string, path: string, ref: string) =>
    ["history", repository, path, ref] as const,
  pull: (repository: string, branch: string) =>
    ["pull", repository, branch] as const,
  people: (logins: string[]) => ["people", ...logins] as const,
}

/** The repositories the person shared with Docs, the only ones it can open. */
export function useRepositories() {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.repositories,
    queryFn: () => host.repositories.list(),
    staleTime: Infinity,
  })
}

/** How to show `repository`: its name alone when it is in one of this Open SWE's organizations. */
export function useRepositoryLabel(repository: string): string {
  const shared = useRepositories().data?.find(
    (one) => one.fullName === repository
  )
  return shared ? repositoryLabel(shared) : repository
}

/** Asks the person to share a repository; resolves to it, or `null` if they cancel. */
export function usePickRepository() {
  const host = usePluginHost()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (suggested?: string) => {
      const [picked] = await host.repositories.pick(
        suggested ? { suggested: [suggested] } : {}
      )
      await queryClient.invalidateQueries({ queryKey: keys.repositories })
      return picked ?? null
    },
  })
}

export function useSpace(repository: string) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.space(repository),
    queryFn: async () => {
      const github = host.github(repository)
      const info = await github.info()
      const sha = await github.ref(info.defaultBranch)
      if (!sha) throw new Error(`${repository} has no ${info.defaultBranch}`)
      const tree = await github.tree(sha)
      return {
        defaultBranch: info.defaultBranch,
        truncated: tree.truncated,
        pages: tree.entries
          .filter((entry) => entry.type === "blob" && isMarkdown(entry.path))
          .map((entry) => entry.path)
          .sort((a, b) => a.localeCompare(b)),
      }
    },
    staleTime: 5 * 60_000,
  })
}

/** Refetches this repository's drafts and comments whenever anyone writes one. */
export function useRepositoryWatch(repository: string) {
  const host = usePluginHost()
  const queryClient = useQueryClient()
  useEffect(() => {
    const offDrafts = draftsIn(host, repository).watch(
      () =>
        void queryClient.invalidateQueries({
          queryKey: keys.drafts(repository),
        })
    )
    const offComments = commentsIn(host, repository).watch(
      () =>
        void queryClient.invalidateQueries({
          queryKey: ["comments", repository],
        })
    )
    return () => {
      offDrafts()
      offComments()
    }
  }, [host, queryClient, repository])
}

export function useDrafts(repository: string) {
  const drafts = draftsIn(usePluginHost(), repository)
  return useQuery({
    queryKey: keys.drafts(repository),
    queryFn: async () =>
      new Map(
        (await drafts.query()).map((draft) => [draft.key, draft.document])
      ),
  })
}

export function useHead(repository: string, ref: string) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.head(repository, ref),
    // `null` once the branch is gone, for example deleted when its pull request merged.
    queryFn: () => host.github(repository).ref(ref),
    refetchOnWindowFocus: true,
    staleTime: 0,
  })
}

export function useFile(
  repository: string,
  path: string,
  sha: string | undefined
) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.file(repository, path, sha ?? ""),
    queryFn: () => host.github(repository).file(path, sha!),
    enabled: Boolean(sha),
    staleTime: Infinity,
  })
}

export function useComments(repository: string, path: string) {
  const comments = commentsIn(usePluginHost(), repository)
  return useQuery({
    queryKey: keys.comments(repository, path),
    queryFn: () =>
      comments.query({ where: { path }, orderBy: "createdAt", limit: 500 }),
  })
}

export function useHistory(repository: string, path: string, ref: string) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.history(repository, path, ref),
    queryFn: () => host.github(repository).history(path, ref, 30),
  })
}

export function usePull(repository: string, branch: string | undefined) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.pull(repository, branch ?? ""),
    queryFn: () => host.github(repository).findPull(branch!),
    enabled: Boolean(branch),
  })
}

export interface Profile extends Person {
  /** An object URL for `avatar`, valid while the profile is cached. */
  picture: string | null
}

const pictures = new Map<Blob, string>()

function profile(person: Person): Profile {
  let picture: string | null = null
  if (person.avatar) {
    picture = pictures.get(person.avatar) ?? URL.createObjectURL(person.avatar)
    pictures.set(person.avatar, picture)
  }
  return { ...person, picture }
}

/** Names and pictures for `logins`, keyed by lowercased login. */
export function usePeople(logins: string[]): Map<string, Profile> {
  const host = usePluginHost()
  const sorted = useMemo(
    () => [...new Set(logins.map((login) => login.toLowerCase()))].sort(),
    [logins]
  )
  const people = useQuery({
    queryKey: keys.people(sorted),
    queryFn: () => host.people.get(sorted),
    enabled: sorted.length > 0,
    staleTime: Infinity,
  })
  return useMemo(
    () =>
      new Map(
        (people.data ?? []).map((person) => [
          person.login.toLowerCase(),
          profile(person),
        ])
      ),
    [people.data]
  )
}

export function selfProfile(host: PluginHost): Profile {
  return profile(host.context.user)
}

/** Names the web UI's tab after what is open; `null` leaves it named Docs. */
export function useTabTitle(title: string | null) {
  const host = usePluginHost()
  useEffect(() => {
    void host.ui.setTitle(title)
  }, [host, title])
}
