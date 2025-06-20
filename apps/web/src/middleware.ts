import { NextRequest, NextResponse } from "next/server";
import { GITHUB_TOKEN_COOKIE } from "@open-swe/shared/constants";

export function middleware(request: NextRequest) {
  if (request.nextUrl.pathname === "/") {
    const token = request.cookies.get(GITHUB_TOKEN_COOKIE)?.value;
    if (token && token.length > 0) {
      const url = request.nextUrl.clone();
      url.pathname = "/chat";
      return NextResponse.redirect(url);
    }
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/"],
};
