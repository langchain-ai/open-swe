import { NextRequest, NextResponse } from "next/server";
import jsonwebtoken from "jsonwebtoken";
import { SESSION_COOKIE } from "@openswe/shared/constants";

export interface SessionUser {
  login: string;
  avatar_url: string;
  html_url: string;
  name: string | null;
  email: string | null;
}

export interface SessionData {
  accessToken: string;
  tokenType: string;
  user: SessionUser;
}

const SESSION_SECRET = process.env.SESSION_SECRET || "development-secret";

function getCookieOptions(expires?: Date) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    maxAge: expires ? undefined : 60 * 60 * 24 * 30,
    expires,
    path: "/",
  };
}

export function createSession(data: SessionData, response: NextResponse): void {
  const token = jsonwebtoken.sign(data, SESSION_SECRET, {
    expiresIn: "30d",
  });
  response.cookies.set(SESSION_COOKIE, token, getCookieOptions());
}

export function getSession(request: NextRequest): SessionData | null {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) {
    return null;
  }
  try {
    return jsonwebtoken.verify(token, SESSION_SECRET) as SessionData;
  } catch {
    return null;
  }
}

export function clearSession(response: NextResponse): void {
  response.cookies.delete(SESSION_COOKIE);
}

export function isAuthenticated(request: NextRequest): boolean {
  return getSession(request) !== null;
}
