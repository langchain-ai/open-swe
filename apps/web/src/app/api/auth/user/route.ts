import { NextRequest, NextResponse } from "next/server";
import { getGitHubToken, getGitLabToken, getAuthenticatedProvider } from "@/lib/auth";
import { verifyGithubUser } from "@openswe/shared/github/verify-user";
import { GITLAB_BASE_URL } from "@openswe/shared/constants";

export async function GET(request: NextRequest) {
  try {
    const provider = getAuthenticatedProvider(request);

    if (!provider) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }

    if (provider === "github") {
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
    } else if (provider === "gitlab") {
      const token = getGitLabToken(request);
      const baseUrl = request.cookies.get(GITLAB_BASE_URL)?.value || "https://gitlab.com";

      if (!token) {
        return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
      }

      // Fetch GitLab user info
      const response = await fetch(`${baseUrl}/api/v4/user`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      if (!response.ok) {
        return NextResponse.json(
          { error: "Invalid GitLab token" },
          { status: 401 },
        );
      }

      const gitlabUser = await response.json();

      // Return in GitHub-compatible format
      return NextResponse.json({
        user: {
          login: gitlabUser.username,
          avatar_url: gitlabUser.avatar_url,
          html_url: gitlabUser.web_url,
          name: gitlabUser.name,
          email: gitlabUser.email,
        },
      });
    }

    return NextResponse.json({ error: "Unknown provider" }, { status: 500 });
  } catch (error) {
    console.error("Error fetching user info:", error);
    return NextResponse.json(
      { error: "Failed to fetch user info" },
      { status: 500 },
    );
  }
}
