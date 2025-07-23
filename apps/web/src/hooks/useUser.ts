import useSWR from "swr";

interface UserData {
  login: string;
  avatar_url: string;
  html_url: string;
  name: string | null;
  email: string | null;
}

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
  const { data, error, isLoading, mutate } = useSWR<UserData>(
    "user",
    fetchUser,
  );

  return { user: data || null, isLoading, error, mutate };
}
