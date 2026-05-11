import { promises as fs } from 'fs';
import path from 'path';
import os from 'os';
import crypto from 'crypto';
import type { CliConfig, DeploymentConfig } from '@lib/api-types';

export function getConfigPath(): string {
  const override = process.env.OPENSWE_CONFIG;
  if (override && override.length > 0) return override;
  return path.join(os.homedir(), '.openswe', 'config.json');
}

async function ensureParentDir(filePath: string): Promise<void> {
  const dir = path.dirname(filePath);
  await fs.mkdir(dir, { recursive: true, mode: 0o700 });
  try {
    await fs.chmod(dir, 0o700);
  } catch {
    // best effort
  }
}

export async function loadConfig(): Promise<CliConfig> {
  const p = getConfigPath();
  try {
    const raw = await fs.readFile(p, 'utf-8');
    const parsed = JSON.parse(raw) as Partial<CliConfig>;
    return {
      default: parsed.default,
      deployments: parsed.deployments ?? {},
    };
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') {
      return { deployments: {} };
    }
    throw err;
  }
}

export async function saveConfig(cfg: CliConfig): Promise<void> {
  const p = getConfigPath();
  await ensureParentDir(p);
  const tmp = `${p}.${crypto.randomBytes(6).toString('hex')}.tmp`;
  const data = JSON.stringify(cfg, null, 2);
  await fs.writeFile(tmp, data, { mode: 0o600 });
  try {
    await fs.chmod(tmp, 0o600);
  } catch {
    // best effort
  }
  await fs.rename(tmp, p);
}

export async function getActiveDeployment(
  override?: string,
): Promise<DeploymentConfig | null> {
  const cfg = await loadConfig();
  const keys = Object.keys(cfg.deployments);
  if (keys.length === 0) return null;

  const tryKey = (k: string | undefined): DeploymentConfig | null => {
    if (!k) return null;
    return cfg.deployments[k] ?? null;
  };

  const fromOverride = tryKey(override);
  if (fromOverride) return fromOverride;
  const fromEnv = tryKey(process.env.OPENSWE_BACKEND);
  if (fromEnv) return fromEnv;
  const fromDefault = tryKey(cfg.default);
  if (fromDefault) return fromDefault;
  return cfg.deployments[keys[0]!] ?? null;
}

export async function setDeployment(
  d: DeploymentConfig,
  makeDefault?: boolean,
): Promise<void> {
  const cfg = await loadConfig();
  cfg.deployments[d.backend_url] = d;
  if (makeDefault || !cfg.default) {
    cfg.default = d.backend_url;
  }
  await saveConfig(cfg);
}

export async function removeDeployment(backend_url: string): Promise<void> {
  const cfg = await loadConfig();
  delete cfg.deployments[backend_url];
  if (cfg.default === backend_url) {
    const remaining = Object.keys(cfg.deployments);
    cfg.default = remaining[0];
  }
  await saveConfig(cfg);
}
