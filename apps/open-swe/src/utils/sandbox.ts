import { Sandbox } from "@e2b/code-interpreter";

/**
 * Pauses the sandbox. Either pass an existing sandbox client, or a sandbox session ID.
 * If no sandbox client is provided, the sandbox will be connected to.
 
 * @param sandboxSessionId The ID of the sandbox to pause.
 * @param sandbox The sandbox client to pause. If not provided, the sandbox will be connected to.
 * @returns The sandbox session ID.
 */
export async function pauseSandbox(
  sandboxSessionId: string,
  sandbox?: Sandbox,
): Promise<string> {
  const sandboxClient = sandbox ?? (await Sandbox.connect(sandboxSessionId));
  return await sandboxClient.pause();
}

/**
 * Resumes the sandbox.
 * @param sandboxSessionId The ID of the sandbox to resume.
 * @returns The sandbox client.
 */
export async function resumeSandbox(
  sandboxSessionId: string,
): Promise<Sandbox> {
  return await Sandbox.resume(sandboxSessionId);
}
