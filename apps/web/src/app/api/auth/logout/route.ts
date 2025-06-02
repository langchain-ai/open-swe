import { NextRequest, NextResponse } from "next/server";
import { clearGitHubToken } from "@/lib/auth";

/**
 * API route to handle GitHub logout
 */
export async function POST(request: NextRequest) {
  try {
    const response = NextResponse.json({ success: true });
    clearGitHubToken(response);
    return response;
  } catch (error) {
    console.error("Error during logout:", error);
    return NextResponse.json(
      { success: false, error: "Failed to logout" },
      { status: 500 },
    );
  }
}
