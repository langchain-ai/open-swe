import { describe, it, expect, vi } from 'vitest';
import { promises as fs } from 'fs';
import path from 'path';

async function withTempCwd<T>(
  fn: (cwd: string, logger: typeof import('../logger.js')) => Promise<T>,
): Promise<T> {
  const base = path.join(process.cwd(), '.tmp');
  const tempCwd = path.join(base, `cwd-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  await fs.mkdir(tempCwd, { recursive: true });
  const origCwd = process.cwd();
  try {
    process.chdir(tempCwd);
    vi.resetModules();
    const logger = await import('../logger.js');
    return await fn(tempCwd, logger);
  } finally {
    process.chdir(origCwd);
    await fs.rm(tempCwd, { recursive: true, force: true });
  }
}

describe('logger utils', () => {
  it('initSessionLog creates logs dir and a self-ignoring .gitignore', async () => {
    await withTempCwd(async (cwd, logger) => {
      await logger.initSessionLog();
      const logsDir = path.join(cwd, '.openswe', 'logs');
      const stat = await fs.stat(logsDir);
      expect(stat.isDirectory()).toBe(true);
      const ignore = await fs.readFile(path.join(cwd, '.openswe', '.gitignore'), 'utf8');
      expect(ignore.trim()).toBe('*');
    });
  });

  it('appends info and error entries to the session log file', async () => {
    await withTempCwd(async (_cwd, logger) => {
      await logger.logInfo('hello world');
      await logger.logError('boom');

      const logPath = logger.getLogPath();
      const contents = await fs.readFile(logPath, 'utf-8');
      expect(contents).toContain('INFO: hello world');
      expect(contents).toContain('ERROR: boom');
      expect(contents).toMatch(/openswe session [0-9a-f]+ started/);
    });
  });
});
