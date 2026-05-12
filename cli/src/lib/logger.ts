import { promises as fs } from 'fs';
import path from 'path';
import crypto from 'crypto';

const SESSION_ID = crypto.randomBytes(4).toString('hex');
const STARTED_AT = new Date();

const dirs = () => {
  const root = path.join(process.cwd(), '.openswe');
  const logs = path.join(root, 'logs');
  return { root, logs };
};

const sessionStamp = (d: Date): string =>
  `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}` +
  `-${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}${String(d.getSeconds()).padStart(2, '0')}`;

const logFilePath = (): string =>
  path.join(dirs().logs, `openswe-${sessionStamp(STARTED_AT)}-${SESSION_ID}.log`);

let initPromise: Promise<void> | null = null;

async function init(): Promise<void> {
  const { root, logs } = dirs();
  await fs.mkdir(logs, { recursive: true });
  // Self-ignore: write `*` inside .openswe/.gitignore so the directory's
  // contents are invisible to git without touching the user's root .gitignore.
  const ignorePath = path.join(root, '.gitignore');
  try {
    await fs.access(ignorePath);
  } catch {
    await fs.writeFile(ignorePath, '*\n', 'utf8');
  }
  const header =
    `\n===== openswe session ${SESSION_ID} started ${STARTED_AT.toISOString()} =====\n`;
  await fs.appendFile(logFilePath(), header, 'utf8');
}

async function ensureInit(): Promise<void> {
  if (!initPromise) initPromise = init();
  await initPromise;
}

export async function ensureLogDir(): Promise<void> {
  await ensureInit();
}

export async function initSessionLog(): Promise<void> {
  await ensureInit();
}

export function getLogPath(): string {
  return logFilePath();
}

async function append(level: 'INFO' | 'ERROR', message: string): Promise<void> {
  try {
    await ensureInit();
    const entry = `[${new Date().toISOString()}] ${level}: ${message}\n`;
    await fs.appendFile(logFilePath(), entry, 'utf8');
  } catch {
    // logging must never throw into the UI
  }
}

export async function logInfo(message: string): Promise<void> {
  await append('INFO', message);
}

export async function logError(message: string): Promise<void> {
  await append('ERROR', message);
}
