import { describe, it, expect, vi } from 'vitest';
import { promises as fs } from 'fs';
import path from 'path';

async function withTempHome<T>(fn: (homeDir: string, logger: typeof import('../logger.js')) => Promise<T>): Promise<T> {
  const base = path.join(process.cwd(), '.tmp');
  const tempHome = path.join(base, `home-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  await fs.mkdir(base, { recursive: true });
  await fs.mkdir(tempHome, { recursive: true });
  try {
    vi.stubEnv('HOME', tempHome);
    vi.resetModules();
    const logger = await import('../logger.js');
    return await fn(tempHome, logger);
  } finally {
    vi.unstubAllEnvs();
    await fs.rm(tempHome, { recursive: true, force: true });
  }
}

describe('logger utils', () => {
  it('creates logs directory with ensureLogDir', async () => {
    await withTempHome(async (home, logger) => {
      const logsDir = path.join(home, '.coda', 'logs');
      await logger.ensureLogDir();
      const stat = await fs.stat(logsDir);
      expect(stat.isDirectory()).toBe(true);
    });
  });

  it('appends info and error entries and clears log', async () => {
    await withTempHome(async (home, logger) => {
      const logFile = path.join(home, '.coda', 'logs', 'coda.log');

      // Deterministic timestamps
      vi.useFakeTimers();
      vi.setSystemTime(new Date('2025-01-02T03:04:05.000Z'));

      await logger.ensureLogDir();
      await logger.clearLog();

      await logger.logInfo('hello world');
      await logger.logError('boom');

      const contents = await fs.readFile(logFile, 'utf-8');
      expect(contents).toContain('INFO: hello world');
      expect(contents).toContain('ERROR: boom');
      expect(contents).toMatch(/\[2025-01-02T03:04:05\.000Z\]/);

      // Clear should truncate
      await logger.clearLog();
      const afterClear = await fs.readFile(logFile, 'utf-8');
      expect(afterClear).toBe('');

      vi.useRealTimers();
    });
  });
});
