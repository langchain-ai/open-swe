import { NextRequest, NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";

/**
 * API route to check GitHub authentication status
 */
export async function GET(request: NextRequest) {
  try {
    if (process.env.NEXT_PUBLIC_GITHUB_DISABLED === "true") {
      return NextResponse.json({ authenticated: true });
    }

    const authenticated = isAuthenticated(request);
    return NextResponse.json({ authenticated });
  } catch (error) {
    console.error("Error checking auth status:", error);
    return NextResponse.json(
      { authenticated: false, error: "Failed to check authentication status" },
      { status: 500 },
    );
  }
}
