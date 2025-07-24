import { NextRequest, NextResponse } from "next/server";
import { getInstallationToken } from "@open-swe/shared/github/auth";
import { GITHUB_INSTALLATION_ID_COOKIE } from "@open-swe/shared/constants";

const GITHUB_API_URL = "https://api.github.com";

async function handler(req: NextRequest) {
  const path = req.nextUrl.pathname.replace(/^\/api\/github\/proxy\//, "");
  const installationIdCookie = req.cookies.get(
    GITHUB_INSTALLATION_ID_COOKIE,
  )?.value;

  if (!installationIdCookie) {
    return NextResponse.json(
      { error: `"${GITHUB_INSTALLATION_ID_COOKIE}" cookie is required` },
      { status: 400 },
    );
  }

  const appId = process.env.GITHUB_APP_ID;
  const privateAppKey = process.env.GITHUB_APP_PRIVATE_KEY;

  if (!appId || !privateAppKey) {
    console.error("GitHub App ID or Private App Key is not configured.");
    return NextResponse.json(
      { error: `Missing required environment variables.` },
      { status: 500 },
    );
  }

  try {
    const token = await getInstallationToken(
      installationIdCookie,
      appId,
      privateAppKey,
    );

    const targetUrl = new URL(`${GITHUB_API_URL}/${path}`);

    // Forward query parameters from the original request
    req.nextUrl.searchParams.forEach((value, key) => {
      targetUrl.searchParams.append(key, value);
    });

    const headers = new Headers();
    headers.set("Authorization", `Bearer ${token}`);
    headers.set("Accept", "application/vnd.github.v3+json");
    headers.set("User-Agent", "OpenSWE-Proxy");

    if (req.headers.has("Content-Type")) {
      headers.set("Content-Type", req.headers.get("Content-Type")!);
    }

    const response = await fetch(targetUrl.toString(), {
      method: req.method,
      headers: headers,
      body:
        req.method !== "GET" && req.method !== "HEAD" ? req.body : undefined,
    });

    const responseHeaders = new Headers(response.headers);
    responseHeaders.delete("Content-Encoding"); // Prevent ERR_CONTENT_DECODING_FAILED error.

    return new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("Error in GitHub proxy:", error);
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: "Failed to proxy request to GitHub", details: errorMessage },
      { status: 500 },
    );
  }
}

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const DELETE = handler;
export const PATCH = handler;
