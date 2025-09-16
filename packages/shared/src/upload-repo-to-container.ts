import Docker from "dockerode";
import { spawn } from "node:child_process";
import path from "node:path";

interface UploadOptions {
  containerId: string;
  localRepoPath: string;
  containerPath?: string;
}

export async function uploadRepoToContainer({
  containerId,
  localRepoPath,
  containerPath = "/workspace",
}: UploadOptions): Promise<void> {
  const docker = new Docker();
  try {
    await docker.ping();
  } catch (error) {
    const detail =
      error instanceof Error
        ? error.message
        : typeof error === "string"
          ? error
          : "";
    const suffix =
      detail && detail !== "[object Object]" ? ` Details: ${detail}` : "";
    throw new Error(
      `Docker daemon not running or unreachable. Please start Docker and try again.${suffix}`,
    );
  }
  const container = docker.getContainer(containerId);
  const repoName = path.basename(localRepoPath);
  const tarStream = spawn("tar", [
    "-C",
    path.dirname(localRepoPath),
    "-cf",
    "-",
    repoName,
  ]).stdout;
  if (!tarStream) {
    throw new Error("Failed to create tar stream of repository");
  }
  await container.putArchive(tarStream, { path: containerPath });
}
