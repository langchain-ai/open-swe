import { NextRequest, NextResponse } from "next/server";
import { readdir, stat } from "fs/promises";
import path from "path";

export async function GET(request: NextRequest) {
  try {
    const search = request.nextUrl.searchParams.get("q")?.toLowerCase() ?? "";
    const baseDir = process.env.OPEN_SWE_LOCAL_REPOS_DIR || process.cwd();
    const entries = await readdir(baseDir, { withFileTypes: true });
    const repositories: { name: string; path: string }[] = [];

    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const repoPath = path.join(baseDir, entry.name);
      try {
        await stat(path.join(repoPath, ".git"));
        if (!search || entry.name.toLowerCase().includes(search)) {
          repositories.push({ name: entry.name, path: repoPath });
        }
      } catch {
        // Not a git repository
      }
    }

    return NextResponse.json({ repositories });
  } catch {
    return NextResponse.json(
      { error: "Failed to list local repositories" },
      { status: 500 },
    );
  }
}
