import { NextRequest, NextResponse } from "next/server";
import { getGitLabToken } from "@/lib/auth";
import { GITLAB_BASE_URL } from "@openswe/shared/constants";

export async function GET(request: NextRequest) {
  try {
    const token = getGitLabToken(request);
    const baseUrl = request.cookies.get(GITLAB_BASE_URL)?.value || "https://gitlab.com";

    if (!token) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }

    // Fetch projects from GitLab API
    // membership=true returns only projects the user is a member of
    // per_page=100 gets up to 100 projects (can paginate if needed)
    const response = await fetch(
      `${baseUrl}/api/v4/projects?membership=true&per_page=100&order_by=last_activity_at`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      }
    );

    if (!response.ok) {
      return NextResponse.json(
        { error: "Failed to fetch GitLab projects" },
        { status: response.status }
      );
    }

    const projects = await response.json();

    // Transform to a simpler format
    const transformedProjects = projects.map((project: any) => ({
      id: project.id,
      name: project.name,
      path_with_namespace: project.path_with_namespace, // e.g., "username/project"
      default_branch: project.default_branch || "main",
      web_url: project.web_url,
      namespace: {
        id: project.namespace.id,
        name: project.namespace.name,
        path: project.namespace.path,
      },
    }));

    return NextResponse.json({ projects: transformedProjects });
  } catch (error) {
    console.error("Error fetching GitLab projects:", error);
    return NextResponse.json(
      { error: "Failed to fetch GitLab projects" },
      { status: 500 }
    );
  }
}
