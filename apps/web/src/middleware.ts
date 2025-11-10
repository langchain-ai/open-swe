import { NextRequest, NextResponse } from "next/server";
import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_INSTALLATION_ID_COOKIE,
  GITLAB_TOKEN_COOKIE,
} from "@openswe/shared/constants";
import { verifyGithubUser } from "@openswe/shared/github/verify-user";

export async function middleware(request: NextRequest) {
  // Check for GitHub authentication
  const githubToken = request.cookies.get(GITHUB_TOKEN_COOKIE)?.value;
  const installationId = request.cookies.get(
    GITHUB_INSTALLATION_ID_COOKIE,
  )?.value;
  const githubUser = githubToken && installationId ? await verifyGithubUser(githubToken) : null;

  // Check for GitLab authentication
  const gitlabToken = request.cookies.get(GITLAB_TOKEN_COOKIE)?.value;

  // User is authenticated if they have either GitHub or GitLab auth
  const isAuthenticated = !!githubUser || !!gitlabToken;

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
