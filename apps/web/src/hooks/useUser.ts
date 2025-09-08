import { useEffect } from "react";
import useSWR from "swr";
import { useUserStore, UserData } from "@/stores/user-store";

interface UserResponse {
  user: UserData;
}

interface UseUserResult {
  user: UserData | null;
  isLoading: boolean;
  error: Error | null;
  mutate: () => void;
}

async function fetchUser(): Promise<UserData> {
  const response = await fetch("/api/auth/user");
  if (!response.ok) {
    throw new Error("Failed to fetch user data");
  }
  const data: UserResponse = await response.json();
  return data.user;
}

export function useUser(): UseUserResult {
  const { user, setUser } = useUserStore();
  const { data, error, isLoading, mutate } = useSWR<UserData>(
    "user",
    fetchUser,
  );

  useEffect(() => {
    if (data) {
      setUser(data);
    }
  }, [data, setUser]);

  return {
    user: user || null,
    isLoading,
    error: (error as Error) || null,
    mutate,
  };
}
