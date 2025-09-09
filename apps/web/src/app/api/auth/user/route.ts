import { NextRequest, NextResponse } from "next/server";
import { getSession, type SessionUser } from "@/lib/auth";
import { isLocalModeFromEnv } from "@openswe/shared/open-swe/local-mode";

export async function GET(request: NextRequest) {
  try {
    const session = getSession(request);
    if (!session) {
      if (isLocalModeFromEnv()) {
        const defaultUser: SessionUser = {
          login: "local-user",
          avatar_url: "",
          html_url: "",
          name: null,
          email: null,
        };
        return NextResponse.json({ user: defaultUser });
      }
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }
    return NextResponse.json({ user: session.user });
  } catch {
    return NextResponse.json(
      { error: "Failed to fetch user info" },
      { status: 500 },
    );
  }
}
