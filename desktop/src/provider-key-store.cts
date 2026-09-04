const fs = require("node:fs");
const path = require("node:path");

const PROVIDER_KEY_VARIABLES = [
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
  "GOOGLE_API_KEY",
  "FIREWORKS_API_KEY",
];

function validVariable(value) {
  return typeof value === "string" && PROVIDER_KEY_VARIABLES.includes(value);
}

class ProviderKeyStore {
  options: any;
  keys: Record<string, string>;

  constructor(options) {
    this.options = options;
    this.keys = this.read();
  }

  read() {
    try {
      const serialized = this.options.decryptString(
        fs.readFileSync(this.options.storagePath),
      );
      const value = JSON.parse(serialized);
      if (!value || typeof value !== "object") return {};
      const keys = {};
      for (const [variable, key] of Object.entries(value)) {
        if (validVariable(variable) && typeof key === "string" && key)
          keys[variable] = key;
      }
      return keys;
    } catch {
      return {};
    }
  }

  write() {
    if (!Object.keys(this.keys).length) {
      try {
        fs.unlinkSync(this.options.storagePath);
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
      }
      return;
    }
    const encrypted = this.options.encryptString(JSON.stringify(this.keys));
    fs.mkdirSync(path.dirname(this.options.storagePath), { recursive: true });
    const temporary = `${this.options.storagePath}.${process.pid}.tmp`;
    try {
      fs.writeFileSync(temporary, encrypted, { mode: 0o600 });
      fs.renameSync(temporary, this.options.storagePath);
    } finally {
      fs.rmSync(temporary, { force: true });
    }
  }

  status() {
    return PROVIDER_KEY_VARIABLES.map((variable) => ({
      variable,
      configured: Boolean(this.keys[variable]),
    }));
  }

  env() {
    return { ...this.keys };
  }

  set(variable, value) {
    if (!validVariable(variable)) throw new Error("Unsupported API key");
    const key = typeof value === "string" ? value.trim() : "";
    if (!key || key.length > 512) throw new Error("Enter a valid API key");
    this.keys[variable] = key;
    this.write();
    return this.status();
  }

  clear(variable) {
    if (!validVariable(variable)) throw new Error("Unsupported API key");
    delete this.keys[variable];
    this.write();
    return this.status();
  }
}

module.exports = { PROVIDER_KEY_VARIABLES, ProviderKeyStore };
