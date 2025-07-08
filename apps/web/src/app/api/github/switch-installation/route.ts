import { NextRequest, NextResponse } from "next/server";
import { GITHUB_INSTALLATION_ID_COOKIE } from "@open-swe/shared/constants";

/**
 * Updates the current GitHub installation ID in the HttpOnly cookie
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { installationId } = body;

    if (!installationId || typeof installationId !== "string") {
      return NextResponse.json(
        { error: "Installation ID is required" },
        { status: 400 },
      );
    }

    // Create response and set the new installation ID cookie
    const response = NextResponse.json({ success: true });

    response.cookies.set(GITHUB_INSTALLATION_ID_COOKIE, installationId, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      maxAge: 60 * 60 * 24 * 30, // 30 days
      path: "/",
    });

    return response;
  } catch (error) {
    console.error("Error switching installation ID:", error);
    return NextResponse.json(
      { error: "Failed to switch installation ID" },
      { status: 500 },
    );
  }
}
