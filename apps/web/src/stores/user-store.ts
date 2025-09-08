import { create } from "zustand";

export interface UserData {
  login: string;
  avatar_url: string;
  html_url: string;
  name: string | null;
  email: string | null;
}

interface UserStoreState {
  user: UserData | null;
  setUser: (user: UserData | null) => void;
}

export const useUserStore = create<UserStoreState>((set) => ({
  user: null,
  setUser: (user) => set({ user }),
}));
