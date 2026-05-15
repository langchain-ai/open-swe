import { useQuery } from "@tanstack/react-query";
import { ApiError,  api } from "./api";
import type {SessionUser} from "./api";

export function useSession() {
  return useQuery<SessionUser | null>({
    queryKey: ["session"],
    queryFn: async () => {
      try {
        return await api.me();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
    retry: false,
  });
}
