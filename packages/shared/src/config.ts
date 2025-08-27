import fs from "fs";
import path from "path";
import os from "os";
import readline from "readline";

export interface OpenSWEConfig {
  ANTHROPIC_API_KEY?: string;
  TAVILY_API_KEY?: string;
  LANGSMITH_API_KEY?: string;
}

const REQUIRED_CONFIG_KEYS: (keyof OpenSWEConfig)[] = [
  "ANTHROPIC_API_KEY",
  "TAVILY_API_KEY",
  "LANGSMITH_API_KEY",
];

const CONFIG_DIR = path.join(os.homedir(), ".openswe");
const CONFIG_FILE = path.join(CONFIG_DIR, "config.json");

/**
 * Load configuration from the config file
 */
export function loadConfig(): OpenSWEConfig {
  if (fs.existsSync(CONFIG_FILE)) {
    const configData = fs.readFileSync(CONFIG_FILE, "utf8").trim();
    // Handle empty or invalid config files
    if (configData === "") {
      return {};
    }
    try {
      return JSON.parse(configData);
    } catch {
      return {};
    }
  }

  return {};
}

/**
 * Save configuration to the config file
 */
export function saveConfig(config: OpenSWEConfig): void {
  // Ensure config directory exists
  if (!fs.existsSync(CONFIG_DIR)) {
    fs.mkdirSync(CONFIG_DIR, { recursive: true });
  }

  fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
}

/**
 * Get a configuration value, falling back to environment variable
 */
export function getConfigValue(key: keyof OpenSWEConfig): string | undefined {
  const config = loadConfig();
  return config[key] || process.env[key];
}

/**
 * Check if all required API keys are configured
 */
export function hasRequiredConfig(): boolean {
  const config = loadConfig();

  return REQUIRED_CONFIG_KEYS.every((key) => config[key] || process.env[key]);
}

/**
 * Get missing required configuration keys
 */
export function getMissingConfigKeys(): (keyof OpenSWEConfig)[] {
  const config = loadConfig();

  return REQUIRED_CONFIG_KEYS.filter(
    (key) => !config[key] && !process.env[key],
  );
}

/**
 * Interactively prompt for missing configuration
 */
export async function promptForMissingConfig(): Promise<void> {
  const missing = getMissingConfigKeys();
  if (missing.length === 0) return;

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  const config = loadConfig();

  for (const key of missing) {
    const description = getKeyDescription(key);
    const value = await new Promise<string>((resolve) => {
      rl.question(`Enter your ${description}: `, (answer) => {
        resolve(answer.trim());
      });
    });

    if (value) {
      config[key] = value;
    }
  }

  rl.close();

  if (Object.keys(config).length > 0) {
    saveConfig(config);
  }
}

/**
 * Get user-friendly description for API key
 */
function getKeyDescription(key: keyof OpenSWEConfig): string {
  switch (key) {
    case "ANTHROPIC_API_KEY":
      return "Anthropic API Key (for Claude)";
    case "TAVILY_API_KEY":
      return "Tavily API Key (for web search)";
    case "LANGSMITH_API_KEY":
      return "LangSmith API Key (for tracing)";
    default:
      return key;
  }
}

/**
 * Apply configuration to environment variables
 */
export function applyConfigToEnv(): void {
  const config = loadConfig();

  Object.entries(config).forEach(([key, value]) => {
    if (value && !process.env[key]) {
      process.env[key] = value;
    }
  });
}
