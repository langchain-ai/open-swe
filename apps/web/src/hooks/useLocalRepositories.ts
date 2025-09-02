import useSWR from "swr";

interface LocalRepository {
  name: string;
  path: string;
}

async function fetchLocalRepositories(
  _key: string,
  search: string,
): Promise<LocalRepository[]> {
  const params = new URLSearchParams();
  if (search) {
    params.set("q", search);
  }
  const res = await fetch(`/api/local-repositories?${params.toString()}`);
  if (!res.ok) {
    throw new Error("Failed to fetch local repositories");
  }
  const data: { repositories: LocalRepository[] } = await res.json();
  return data.repositories;
}

export function useLocalRepositories(search: string) {
  const { data, error, isLoading } = useSWR(
    ["local-repositories", search],
    fetchLocalRepositories,
  );
  return {
    repositories: data ?? [],
    isLoading,
    error,
  };
}

export type { LocalRepository };
