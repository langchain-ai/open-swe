import type { Person, PluginHost, StoredDocument } from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo } from "react"

import {
  COMMENTS,
  DRAFTS,
  isMarkdown,
  type Draft,
  type PageComment,
} from "@/lib/model"

export const keys = {
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
  threads: ["threads"] as const,
  people: (logins: string[]) => ["people", ...logins] as const,
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
  useEffect(
    () =>
      host.storage.watch({ repository }, () => {
        void queryClient.invalidateQueries({
          queryKey: keys.drafts(repository),
        })
        void queryClient.invalidateQueries({
          queryKey: ["comments", repository],
        })
      }),
    [host, queryClient, repository]
  )
}

export function useDrafts(repository: string) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.drafts(repository),
    queryFn: async () =>
      new Map(
        (
          (await host.storage.query<Draft>(DRAFTS, {
            repository,
          })) as StoredDocument<Draft>[]
        ).map((draft) => [draft.key, draft.document])
      ),
  })
}

export function useHead(repository: string, ref: string) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.head(repository, ref),
    queryFn: async () => {
      const sha = await host.github(repository).ref(ref)
      if (!sha) throw new Error(`Branch ${ref} no longer exists`)
      return sha
    },
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
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.comments(repository, path),
    queryFn: async () =>
      (await host.storage.query<PageComment>(COMMENTS, {
        repository,
        where: { path },
        limit: 500,
      })) as StoredDocument<PageComment>[],
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

export function useThreads(enabled: boolean) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.threads,
    queryFn: () => host.threads.list(),
    enabled,
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
