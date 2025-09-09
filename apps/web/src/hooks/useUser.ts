import { useEffect } from "react";
import useSWR from "swr";
import { useUserStore, UserData } from "@/stores/user-store";

export const DEFAULT_USER: UserData = {
  login: "local-user",
  avatar_url: "",
  html_url: "",
  name: null,
  email: null,
};

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
  try {
    const response = await fetch("/api/auth/user");
    if (!response.ok) {
      if (process.env.NEXT_PUBLIC_OPEN_SWE_LOCAL_MODE === "true") {
        return DEFAULT_USER;
      }
      throw new Error("Failed to fetch user data");
    }
    const data: UserResponse = await response.json();
    return data.user;
  } catch {
    if (process.env.NEXT_PUBLIC_OPEN_SWE_LOCAL_MODE === "true") {
      return DEFAULT_USER;
    }
    throw new Error("Failed to fetch user data");
  }
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
