import http from "node:http";

const HOST = "127.0.0.1";
const PROVIDER_KEYS: Readonly<Record<string, readonly string[]>> = {
  anthropic: ["ANTHROPIC_API_KEY"],
  fireworks: ["FIREWORKS_API_KEY"],
  google_genai: ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
  openai: ["OPENAI_API_KEY"],
};

export interface CredentialStatus {
  available: boolean;
  variable: string | null;
  canSignIn?: true;
}

export function reservePort(host = HOST): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    const server = http.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, host, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : null;
      server.close((error) =>
        error || !port ? reject(error || new Error("No port")) : resolve(port),
      );
    });
  });
}

export function modelCredentialStatus(
  modelId: unknown,
  env: NodeJS.ProcessEnv,
  options: { openAiOAuth?: boolean } = {},
): CredentialStatus {
  const provider = typeof modelId === "string" ? modelId.split(":", 1)[0]! : "";
  const variables = PROVIDER_KEYS[provider];
  if (!variables) return { available: true, variable: null };
  const variable = variables.find((key) => env[key]);
  const oauthAvailable = provider === "openai" && options.openAiOAuth === true;
  return {
    available: Boolean(variable) || oauthAvailable,
    variable: variable || (oauthAvailable ? null : variables[0]!),
    ...(provider === "openai" && !variable ? { canSignIn: true as const } : {}),
  };
}
