import { NextRequest, NextResponse } from "next/server";
import { getGitHubToken } from "@/lib/auth";
import { verifyGithubUser } from "@open-swe/shared/github/verify-user";

export async function GET(request: NextRequest) {
  try {
    const token = getGitHubToken(request);
    if (!token || !token.access_token) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }
    const user = await verifyGithubUser(token.access_token);
    if (!user) {
      return NextResponse.json(
        { error: "Invalid GitHub token" },
        { status: 401 },
      );
    }
    // Only return safe fields
    return NextResponse.json({
      user: {
        login: user.login,
        avatar_url: user.avatar_url,
        html_url: user.html_url,
        name: user.name,
        email: user.email,
      },
    });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to fetch user info" },
      { status: 500 },
    );
  }
}
