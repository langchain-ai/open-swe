import { NextRequest, NextResponse } from "next/server";
import { clearGitHubToken } from "@/lib/auth";
import { GITLAB_TOKEN_COOKIE, GITLAB_BASE_URL } from "@openswe/shared/constants";

/**
 * API route to handle logout for both GitHub and GitLab
 */
export async function POST(request: NextRequest) {
  try {
    const response = NextResponse.json({ success: true });

    // Clear GitHub tokens
    clearGitHubToken(response);

    // Clear GitLab tokens
    response.cookies.delete(GITLAB_TOKEN_COOKIE);
    response.cookies.delete(GITLAB_BASE_URL);
    response.cookies.delete("gitlab_user_id");
    response.cookies.delete("gitlab_user_login");

    return response;
  } catch (error) {
    console.error("Error during logout:", error);
    return NextResponse.json(
      { success: false, error: "Failed to logout" },
      { status: 500 },
    );
  }
}
