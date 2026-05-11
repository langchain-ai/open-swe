import { describe, it, expect, vi } from 'vitest';
import { promises as fs } from 'fs';
import path from 'path';

async function withTempHome<T>(fn: (homeDir: string, storage: typeof import('../storage.js')) => Promise<T>): Promise<T> {
  const base = path.join(process.cwd(), '.tmp');
  const tempHome = path.join(base, `home-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  await fs.mkdir(base, { recursive: true });
  await fs.mkdir(tempHome, { recursive: true });
  try {
    vi.stubEnv('HOME', tempHome);
    vi.resetModules();
    const storage = await import('../storage.js');
    return await fn(tempHome, storage);
  } finally {
    vi.unstubAllEnvs();
    await fs.rm(tempHome, { recursive: true, force: true });
  }
}

describe('storage utils', () => {
  it('stores, reads, and deletes API keys by provider', async () => {
    await withTempHome(async (home, storage) => {
      const authPath = path.join(home, '.coda', 'auth.json');

      await expect(storage.getStoredApiKey('openai')).resolves.toBeNull();
      await expect(storage.getStoredApiKey('anthropic')).resolves.toBeNull();

      await storage.storeApiKey('openai', 'sk-openai-123');
      const contents1 = JSON.parse(await fs.readFile(authPath, 'utf-8'));
      expect(contents1['openai']).toBe('sk-openai-123');
      await expect(storage.getStoredApiKey('openai')).resolves.toBe('sk-openai-123');

      await storage.storeApiKey('anthropic', 'sk-ant-456');
      const contents2 = JSON.parse(await fs.readFile(authPath, 'utf-8'));
      expect(contents2['openai']).toBe('sk-openai-123');
      expect(contents2['anthropic']).toBe('sk-ant-456');

      await storage.deleteStoredApiKey('openai');
      await expect(storage.getStoredApiKey('openai')).resolves.toBeNull();
      await expect(storage.getStoredApiKey('anthropic')).resolves.toBe('sk-ant-456');

      await storage.deleteAllApiKeys();
      await expect(storage.getStoredApiKey('anthropic')).resolves.toBeNull();
    });
  });

  it('retrieves all API keys at once', async () => {
    await withTempHome(async (home, storage) => {
      await storage.storeApiKey('openai', 'sk-openai');
      await storage.storeApiKey('google', 'sk-google');

      const keys = await storage.getStoredApiKeys();
      expect(keys.openai).toBe('sk-openai');
      expect(keys.google).toBe('sk-google');
      expect(keys.anthropic).toBeUndefined();
    });
  });

  it('stores and retrieves model configuration; missing returns null', async () => {
    await withTempHome(async (home, storage) => {
      const configPath = path.join(home, '.coda', 'config.json');

      await expect(storage.getStoredModelConfig()).resolves.toBeNull();

      const modelConfig = { name: 'gpt-5.5', provider: 'openai', effort: 'medium' } as const;
      await storage.storeModelConfig(modelConfig);
      const raw = JSON.parse(await fs.readFile(configPath, 'utf-8'));
      expect(raw.modelConfig).toEqual(modelConfig);
      await expect(storage.getStoredModelConfig()).resolves.toEqual(modelConfig);
    });
  });

  it('saves and loads sessions; missing session returns empty array', async () => {
    await withTempHome(async (home, storage) => {
      const sessionId = 'abc123';
      const sessionPath = path.join(home, '.coda', 'sessions', `${sessionId}.json`);

      await expect(storage.loadSession(sessionId)).resolves.toEqual([]);

      const history = [
        { type: 'human', content: 'Hello' },
        { type: 'ai', content: 'Hi there!' },
      ];
      await storage.saveSession(sessionId, history as any);
      const saved = JSON.parse(await fs.readFile(sessionPath, 'utf-8'));
      expect(saved).toEqual(history);

      await expect(storage.loadSession(sessionId)).resolves.toEqual(history);
    });
  });
});
