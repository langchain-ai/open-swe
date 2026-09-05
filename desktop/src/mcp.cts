const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { execFile } = require("node:child_process");
const { promisify, isDeepStrictEqual } = require("node:util");
const { randomBytes, createHash, timingSafeEqual } = require("node:crypto");

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return fallback;
    throw new Error(`Cannot read MCP configuration: ${file}`);
  }
}

function atomicWrite(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${randomBytes(8).toString("hex")}.tmp`;
  try {
    fs.writeFileSync(temporary, value, { mode: 0o600, flag: "wx" });
    fs.renameSync(temporary, file);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

function object(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

function text(value) {
  return typeof value === "string" && !value.includes("\0");
}

function validateServer(name, server) {
  if (!text(name) || !name.trim() || name.length > 100 || !object(server))
    throw new Error("Invalid MCP server");
  const allowed = new Set([
    "command",
    "args",
    "env",
    "cwd",
    "env_vars",
    "env_passthrough",
    "url",
    "headers",
    "auth_type",
    "oauth_client_id",
    "oauth_scope",
    "oauth_redirect_uri",
    "oauth_token_endpoint_auth_method",
    "enabled",
    "transport",
  ]);
  if (Object.keys(server).some((key) => !allowed.has(key)))
    throw new Error("Unsupported MCP configuration field");
  for (const key of ["oauth_client_id", "oauth_scope", "oauth_redirect_uri"]) {
    if (server[key] !== undefined && !text(server[key]))
      throw new Error(`Invalid MCP ${key}`);
  }
  if (
    server.oauth_token_endpoint_auth_method !== undefined &&
    !["none", "client_secret_basic", "client_secret_post"].includes(
      server.oauth_token_endpoint_auth_method,
    )
  )
    throw new Error("Invalid OAuth token endpoint authentication method");
  if (server.oauth_redirect_uri) {
    const redirect = new URL(server.oauth_redirect_uri);
    if (
      redirect.protocol !== "http:" ||
      redirect.hostname !== "127.0.0.1" ||
      !redirect.port ||
      Number(redirect.port) === 0 ||
      redirect.pathname !== "/callback" ||
      redirect.username ||
      redirect.password ||
      redirect.search ||
      redirect.hash
    )
      throw new Error(
        "OAuth redirect URI must be http://127.0.0.1:PORT/callback",
      );
  }
  if (server.enabled !== undefined && typeof server.enabled !== "boolean")
    throw new Error("Invalid MCP enabled flag");
  if (server.url !== undefined) {
    const url = new URL(server.url);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.hash ||
      server.command
    )
      throw new Error("Invalid MCP HTTP URL");
  } else if (!text(server.command) || !server.command.trim()) {
    throw new Error("MCP command is required");
  }
  for (const key of ["args", "env_vars", "env_passthrough"]) {
    if (
      server[key] !== undefined &&
      (!Array.isArray(server[key]) || !server[key].every(text))
    )
      throw new Error(`Invalid MCP ${key}`);
  }
  for (const key of ["env", "headers"]) {
    if (
      server[key] !== undefined &&
      (!object(server[key]) ||
        !Object.entries(server[key]).every(
          ([field, value]) => text(field) && text(value),
        ))
    )
      throw new Error(`Invalid MCP ${key}`);
  }
  if (server.cwd !== undefined && !text(server.cwd))
    throw new Error("Invalid MCP cwd");
  if (
    server.auth_type !== undefined &&
    !["none", "headers", "oauth"].includes(server.auth_type)
  )
    throw new Error("Invalid local MCP authentication type");
  if (
    ["tokens", "access_token", "refresh_token", "oauth_client_secret"].some(
      (key) => key in server,
    )
  )
    throw new Error("OAuth credentials must use secure storage");
  return server;
}

async function resolveLoginEnvironment(
  env = process.env,
  run = promisify(execFile),
) {
  if (process.platform === "win32") return { ...env };
  const shell = env.SHELL || "/bin/sh";
  const marker = "OPEN_SWE_LOGIN_ENV_START";
  const { stdout } = await run(
    shell,
    ["-ilc", `printf '${marker}\\0'; /usr/bin/env -0`],
    {
      env,
      encoding: "utf8",
      timeout: 10000,
      maxBuffer: 2 * 1024 * 1024,
    },
  );
  const start = stdout.indexOf(`${marker}\0`);
  if (start < 0)
    throw new Error("Login shell environment could not be resolved");
  const result = { ...env };
  for (const entry of stdout.slice(start + marker.length + 1).split("\0")) {
    const separator = entry.indexOf("=");
    if (separator > 0)
      result[entry.slice(0, separator)] = entry.slice(separator + 1);
  }
  return result;
}

class DesktopMcp {
  options: any;
  broker: any;
  url: string;
  secret: string;
  constructor(options) {
    this.options = options;
    this.secret = randomBytes(32).toString("base64url");
  }
  document() {
    const document = readJson(this.options.configPath, { mcpServers: {} });
    if (!object(document) || !object(document.mcpServers))
      throw new Error("Expected an mcpServers map");
    for (const [name, server] of Object.entries(document.mcpServers))
      validateServer(name, server);
    return document;
  }
  servers() {
    const toggles = readJson(this.options.togglesPath, {});
    if (
      !object(toggles) ||
      Object.values(toggles).some((value) => typeof value !== "boolean")
    )
      throw new Error("Invalid MCP enable switches");
    return Object.entries(this.document().mcpServers).map(([name, value]) => {
      const server: any = value;
      return {
        ...server,
        name,
        transport: server.url ? "streamable_http" : "stdio",
        enabled:
          (Object.hasOwn(toggles, name) ? toggles[name] : undefined) ??
          server.enabled ??
          true,
        oauth_client_secret_configured: fs.existsSync(
          `${this.credentialPath(name, server)}.secret`,
        ),
      };
    });
  }
  save(input) {
    if (!object(input)) throw new Error("Invalid MCP server");
    const {
      name,
      enabled = true,
      transport: _transport,
      oauth_client_secret: clientSecret,
      oauth_client_secret_configured: _secretConfigured,
      ...server
    } = input;
    validateServer(name, { ...server, enabled });
    if (clientSecret !== undefined && !text(clientSecret))
      throw new Error("Invalid MCP client secret");
    const document = this.document();
    const previous = Object.hasOwn(document.mcpServers, name)
      ? document.mcpServers[name]
      : undefined;
    const comparable = previous ? { ...previous } : undefined;
    if (comparable) {
      delete comparable.enabled;
      delete comparable.transport;
    }
    const changed = !isDeepStrictEqual(
      comparable,
      JSON.parse(JSON.stringify(server)),
    );
    const secretFile = `${this.credentialPath(name, server)}.secret`;
    let encryptedSecret;
    if (clientSecret) {
      if (
        !server.url ||
        server.auth_type !== "oauth" ||
        !server.oauth_client_id
      )
        throw new Error(
          "A client secret requires an OAuth server and client ID",
        );
      encryptedSecret = this.options.encryptString(clientSecret);
    } else if (
      previous &&
      changed &&
      server.auth_type === "oauth" &&
      previous.url === server.url &&
      previous.oauth_client_id === server.oauth_client_id
    ) {
      const previousSecret = `${this.credentialPath(name, previous)}.secret`;
      if (fs.existsSync(previousSecret))
        encryptedSecret = fs.readFileSync(previousSecret);
    }
    if (changed) {
      Object.defineProperty(document.mcpServers, name, {
        value: server,
        enumerable: true,
        configurable: true,
        writable: true,
      });
      atomicWrite(
        this.options.configPath,
        `${JSON.stringify(document, null, 2)}\n`,
      );
    }
    const toggles = readJson(this.options.togglesPath, {});
    Object.defineProperty(toggles, name, {
      value: enabled,
      enumerable: true,
      configurable: true,
      writable: true,
    });
    atomicWrite(this.options.togglesPath, JSON.stringify(toggles));
    if (previous && (changed || clientSecret))
      this.clearCredentials(name, previous);
    if (encryptedSecret) atomicWrite(secretFile, encryptedSecret);
    return true;
  }
  delete(name) {
    const document = this.document();
    if (!Object.hasOwn(document.mcpServers, name)) return false;
    this.clearCredentials(name, document.mcpServers[name]);
    delete document.mcpServers[name];
    atomicWrite(
      this.options.configPath,
      `${JSON.stringify(document, null, 2)}\n`,
    );
    const toggles = readJson(this.options.togglesPath, {});
    delete toggles[name];
    atomicWrite(this.options.togglesPath, JSON.stringify(toggles));
    return true;
  }
  credentialPath(name, server) {
    const key = createHash("sha256")
      .update(
        JSON.stringify([
          name,
          server.url,
          server.oauth_client_id,
          server.oauth_scope,
          server.oauth_redirect_uri,
          server.oauth_token_endpoint_auth_method,
        ]),
      )
      .digest("hex");
    return path.join(this.options.credentialsDir, `${key}.bin`);
  }
  clearCredentials(name, server) {
    fs.rmSync(this.credentialPath(name, server), { force: true });
    fs.rmSync(`${this.credentialPath(name, server)}.secret`, { force: true });
  }
  credentials(name, key, value?) {
    const server = this.servers().find(
      (entry) => entry.name === name && entry.url && entry.enabled,
    );
    if (!server) throw new Error("MCP server is unavailable");
    const file = this.credentialPath(name, server);
    if (key !== path.basename(file))
      throw new Error("MCP configuration changed; reconnect");
    if (value !== undefined) {
      atomicWrite(file, this.options.encryptString(JSON.stringify(value)));
      return null;
    }
    try {
      const record = fs.existsSync(file)
        ? JSON.parse(this.options.decryptString(fs.readFileSync(file)))
        : {};
      if (fs.existsSync(`${file}.secret`))
        record.client_secret = this.options.decryptString(
          fs.readFileSync(`${file}.secret`),
        );
      return record;
    } catch {
      throw new Error("MCP secure credentials are unavailable");
    }
  }
  backendEnv() {
    return {
      OPEN_SWE_MCP_BROKER_URL: this.url,
      OPEN_SWE_MCP_BROKER_TOKEN: this.secret,
    };
  }
  async start() {
    this.broker = http.createServer(async (request, response) => {
      response.setHeader("Cache-Control", "no-store");
      const expected = Buffer.from(`Bearer ${this.secret}`);
      const supplied = Buffer.from(request.headers.authorization || "");
      if (
        request.headers.origin ||
        supplied.length !== expected.length ||
        !timingSafeEqual(expected, supplied)
      ) {
        response.writeHead(403).end();
        return;
      }
      try {
        let result;
        if (request.method === "GET" && request.url === "/runtime") {
          result = {
            servers: this.servers().map((server) => ({
              ...server,
              credential_key: path.basename(
                this.credentialPath(server.name, server),
              ),
            })),
            env: this.options.loginEnv,
            cloud: await this.options.cloudRuntime(),
          };
        } else if (
          request.method === "POST" &&
          ["/credentials", "/open"].includes(request.url)
        ) {
          let body = "";
          for await (const chunk of request) {
            body += chunk;
            if (Buffer.byteLength(body) > 128 * 1024)
              throw new Error("Request too large");
          }
          const data = JSON.parse(body);
          if (request.url === "/credentials")
            result = this.credentials(data.name, data.key, data.value);
          else {
            const url = new URL(data.url);
            if (
              !["http:", "https:"].includes(url.protocol) ||
              url.username ||
              url.password
            )
              throw new Error("Invalid authorization URL");
            await this.options.openExternal(url.toString());
            result = true;
          }
        } else {
          response.writeHead(404).end();
          return;
        }
        response
          .writeHead(200, { "Content-Type": "application/json" })
          .end(JSON.stringify(result));
      } catch {
        response.writeHead(400).end("Local MCP operation failed");
      }
    });
    this.broker.requestTimeout = 15000;
    await new Promise<void>((resolve, reject) => {
      this.broker.once("error", reject);
      this.broker.listen(0, "127.0.0.1", resolve);
    });
    this.url = `http://127.0.0.1:${this.broker.address().port}`;
  }
  async close() {
    if (!this.broker) return;
    this.broker.closeAllConnections();
    await new Promise<void>((resolve) => this.broker.close(() => resolve()));
  }
}

module.exports = { DesktopMcp, resolveLoginEnvironment, validateServer };
