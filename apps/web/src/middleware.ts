import { NextRequest, NextResponse } from "next/server";
import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_INSTALLATION_ID_COOKIE,
} from "@openswe/shared/constants";
// Note: Avoid Node-only libraries in Edge runtime. Do not import Octokit here.

export async function middleware(request: NextRequest) {
  const token = request.cookies.get(GITHUB_TOKEN_COOKIE)?.value;
  const installationId = request.cookies.get(
    GITHUB_INSTALLATION_ID_COOKIE,
  )?.value;
  // Edge-safe check: presence of cookies indicates authenticated session
  const isAuthenticated = Boolean(token && installationId);

  if (request.nextUrl.pathname === "/") {
    if (isAuthenticated) {
      const url = request.nextUrl.clone();
      url.pathname = "/chat";
      return NextResponse.redirect(url);
    }
  }

  if (request.nextUrl.pathname.startsWith("/chat")) {
    if (!isAuthenticated) {
      const url = request.nextUrl.clone();
      url.pathname = "/";
      return NextResponse.redirect(url);
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/", "/chat/:path*"],
};
