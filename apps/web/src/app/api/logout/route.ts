import { NextRequest, NextResponse } from "next/server";
import { clearSession } from "@/lib/auth";

/**
 * API route to handle user logout
 */
export async function POST(request: NextRequest) {
  try {
    const response = NextResponse.json({ success: true });
    clearSession(response);
    return response;
  } catch {
    return NextResponse.json(
      { success: false, error: "Failed to logout" },
      { status: 500 },
    );
  }
}
