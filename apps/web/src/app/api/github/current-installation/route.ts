import { NextRequest, NextResponse } from "next/server";
import { GITHUB_INSTALLATION_ID_COOKIE } from "@open-swe/shared/constants";

/**
 * Gets the current GitHub installation ID from the HttpOnly cookie
 */
export async function GET(request: NextRequest) {
  try {
    const installationId = request.cookies.get(
      GITHUB_INSTALLATION_ID_COOKIE,
    )?.value;

    return NextResponse.json({
      installationId: installationId || null,
    });
  } catch (error) {
    console.error("Error getting current installation ID:", error);
    return NextResponse.json(
      { error: "Failed to get current installation ID" },
      { status: 500 },
    );
  }
}
