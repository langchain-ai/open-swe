import { promises as fs } from 'fs';
import path from 'path';
import os from 'os';

const STORAGE_DIR = path.join(os.homedir(), '.coda');
const LOGS_DIR = path.join(STORAGE_DIR, 'logs');
const LOG_FILE = path.join(LOGS_DIR, 'coda.log');

export async function ensureLogDir(): Promise<void> {
  await fs.mkdir(LOGS_DIR, { recursive: true });
}

export async function clearLog(): Promise<void> {
  await ensureLogDir();
  await fs.writeFile(LOG_FILE, '');
}

export async function logInfo(message: string): Promise<void> {
  const timestamp = new Date().toISOString();
  const entry = `[${timestamp}] INFO: ${message}\n`;
  await fs.appendFile(LOG_FILE, entry, 'utf8');
}

export async function logError(message: string): Promise<void> {
  const timestamp = new Date().toISOString();
  const entry = `[${timestamp}] ERROR: ${message}\n`;
  await fs.appendFile(LOG_FILE, entry, 'utf8');
}

