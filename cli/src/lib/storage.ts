import { promises as fs } from 'fs';
import path from 'path';
import os from 'os';
import { BaseMessage } from '@langchain/core/messages';
import type { ModelConfig, Provider, ApiKeys } from '@types';
import { logError } from '@lib/logger';
import { isKnownModelConfig } from '@lib/models.js';

const STORAGE_DIR = path.join(os.homedir(), '.coda');
const AUTH_FILE = path.join(STORAGE_DIR, 'auth.json');
const CONFIG_FILE = path.join(STORAGE_DIR, 'config.json');
const SESSIONS_DIR = path.join(STORAGE_DIR, 'sessions');

async function ensureStorageDirs(): Promise<void> {
  try {
    await fs.mkdir(STORAGE_DIR, { recursive: true });
    await fs.mkdir(SESSIONS_DIR, { recursive: true });
  } catch (error) {
    await logError(`Failed to create storage directories: ${error}`);
  }
}

export async function storeApiKey(provider: Provider, key: string): Promise<void> {
  await ensureStorageDirs();
  try {
    let authData: Record<string, string> = {};
    try {
      const data = await fs.readFile(AUTH_FILE, 'utf-8');
      authData = JSON.parse(data);
    } catch (error: any) {
      if (error.code !== 'ENOENT') {
        throw error;
      }
    }
    authData[provider] = key;
    await fs.writeFile(AUTH_FILE, JSON.stringify(authData, null, 2), {
      mode: 0o600,
    });
  } catch (error) {
    await logError(`Failed to store API key: ${error}`);
  }
}

export async function getStoredApiKey(provider: Provider): Promise<string | null> {
  try {
    const data = await fs.readFile(AUTH_FILE, 'utf-8');
    const authData = JSON.parse(data);
    return authData[provider] || null;
  } catch (error) {
    return null;
  }
}

export async function getStoredApiKeys(): Promise<ApiKeys> {
  try {
    const data = await fs.readFile(AUTH_FILE, 'utf-8');
    const authData = JSON.parse(data);
    return {
      openai: authData.openai || undefined,
      anthropic: authData.anthropic || undefined,
      google: authData.google || undefined,
    };
  } catch (error) {
    return {};
  }
}

export async function deleteStoredApiKey(provider: Provider): Promise<void> {
  try {
    const data = await fs.readFile(AUTH_FILE, 'utf-8');
    const authData = JSON.parse(data);
    delete authData[provider];
    await fs.writeFile(AUTH_FILE, JSON.stringify(authData, null, 2), {
      mode: 0o600,
    });
  } catch (error: any) {
    if (error.code !== 'ENOENT') {
      await logError(`Failed to delete API key: ${error}`);
    }
  }
}

export async function deleteAllApiKeys(): Promise<void> {
  try {
    await fs.unlink(AUTH_FILE);
  } catch (error: any) {
    if (error.code !== 'ENOENT') {
      await logError(`Failed to delete API keys: ${error}`);
    }
  }
}

export async function storeModelConfig(modelConfig: ModelConfig): Promise<void> {
  await ensureStorageDirs();
  try {
    let configData: Record<string, unknown> = {};
    try {
      const data = await fs.readFile(CONFIG_FILE, 'utf-8');
      configData = JSON.parse(data);
    } catch (error: any) {
      if (error.code !== 'ENOENT') {
        throw error;
      }
    }
    const updated = { ...configData, modelConfig };
    await fs.writeFile(CONFIG_FILE, JSON.stringify(updated, null, 2));
  } catch (error) {
    await logError(`Failed to store model configuration: ${error}`);
  }
}

export async function getStoredModelConfig(): Promise<ModelConfig | null> {
  try {
    const data = await fs.readFile(CONFIG_FILE, 'utf-8');
    const parsed = JSON.parse(data);
    const stored = parsed.modelConfig;
    if (stored && typeof stored.name === 'string' && typeof stored.effort === 'string' && typeof stored.provider === 'string') {
      if (!isKnownModelConfig(stored.name, stored.effort)) {
        return null;
      }
      return { name: stored.name, provider: stored.provider, effort: stored.effort };
    }
    return null;
  } catch (error) {
    return null;
  }
}

export async function saveSession(sessionId: string, history: BaseMessage[]): Promise<void> {
  await ensureStorageDirs();
  const sessionFile = path.join(SESSIONS_DIR, `${sessionId}.json`);
  try {
    await fs.writeFile(sessionFile, JSON.stringify(history, null, 2));
  } catch (error) {
    await logError(`Failed to save session ${sessionId}: ${error}`);
  }
}

export async function loadSession(sessionId: string): Promise<BaseMessage[]> {
  const sessionFile = path.join(SESSIONS_DIR, `${sessionId}.json`);
  try {
    const data = await fs.readFile(sessionFile, 'utf-8');
    return JSON.parse(data);
  } catch (error) {
    return [];
  }
}

