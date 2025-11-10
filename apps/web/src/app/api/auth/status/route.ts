import { NextRequest, NextResponse } from "next/server";
import { isAuthenticated, getAuthenticatedProvider } from "@/lib/auth";

/**
 * API route to check authentication status for both GitHub and GitLab
 */
export async function GET(request: NextRequest) {
  try {
    const authenticated = isAuthenticated(request);
    const provider = getAuthenticatedProvider(request);
    return NextResponse.json({ authenticated, provider });
  } catch (error) {
    console.error("Error checking auth status:", error);
    return NextResponse.json(
      { authenticated: false, provider: null, error: "Failed to check authentication status" },
      { status: 500 },
    );
  }
}
