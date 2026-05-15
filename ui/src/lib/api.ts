/**
 * Typed client for the open-swe dashboard backend.
 *
 * All requests are sent with credentials so the httpOnly `osw_session`
 * cookie set by the OAuth callback rides along on cross-origin calls.
 */

const API_BASE = (import.meta.env.VITE_DASHBOARD_API_BASE_URL ?? "").replace(/\/$/, "");

if (!API_BASE && typeof window !== "undefined") {
  console.warn("VITE_DASHBOARD_API_BASE_URL is not set");
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}/dashboard/api${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface SessionUser {
  login: string;
  email: string | null;
  avatar_url: string | null;
  is_admin: boolean;
}

export interface ModelOption {
  id: string;
  label: string;
  efforts: Array<string>;
  default_effort: string;
}

export interface Profile {
  login?: string;
  email?: string;
  default_model?: string;
  reasoning_effort?: string;
  default_repo?: string | null;
  updated_at?: string;
}

export interface ProfileUpdate {
  default_model: string;
  reasoning_effort: string;
  default_repo?: string | null;
}

export interface Repository {
  full_name: string;
  private: boolean;
}

export interface Installation {
  id: number;
  account: string | null;
  account_type: string | null;
}

export interface ReposPayload {
  installations: Array<Installation>;
  repositories: Array<Repository>;
}

export const api = {
  me: () => request<SessionUser>("/me"),
  options: () => request<{ models: Array<ModelOption> }>("/options"),
  profile: () => request<Profile>("/profile"),
  saveProfile: (body: ProfileUpdate) =>
    request<Profile>("/profile", { method: "PUT", body: JSON.stringify(body) }),
  repos: () => request<ReposPayload>("/repos"),
  adminListProfiles: () => request<Array<Profile>>("/admin/profiles"),
  adminSaveProfile: (login: string, body: ProfileUpdate & { email?: string }) =>
    request<Profile>(`/admin/profiles/${encodeURIComponent(login)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
};

export function loginUrl(redirectTo?: string): string {
  const target = redirectTo ?? (typeof window !== "undefined" ? window.location.origin : "");
  const qs = target ? `?redirect_to=${encodeURIComponent(target)}` : "";
  return `${API_BASE}/dashboard/api/auth/login${qs}`;
}
