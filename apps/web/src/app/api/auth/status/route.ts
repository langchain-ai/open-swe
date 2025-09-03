import { NextRequest, NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";

/**
 * API route to check GitHub authentication status
 */
export async function GET(request: NextRequest) {
  if (process.env.NEXT_PUBLIC_GITHUB_DISABLED === "true") {
    return NextResponse.json({ authenticated: true });
  }
  try {
    const authenticated = isAuthenticated(request);
    return NextResponse.json({ authenticated });
  } catch {
    return NextResponse.json(
      { authenticated: false, error: "Failed to check authentication status" },
      { status: 500 },
    );
  }
}
