import { promises as fs } from 'fs';
import path from 'path';
import { SEARCH_RESULTS_LIMIT } from './constants.js';

const IGNORED_DIRS = new Set(['node_modules', '.git', 'dist']);

async function* walk(dir: string): AsyncGenerator<string> {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!IGNORED_DIRS.has(entry.name)) {
        yield* walk(fullPath);
      }
    } else {
      yield fullPath;
    }
  }
}

export async function searchFiles(query: string, rootDir: string): Promise<string[]> {
  const results: string[] = [];
  try {
    for await (const filePath of walk(rootDir)) {
      const relativePath = path.relative(rootDir, filePath);
      if (relativePath.toLowerCase().includes(query.toLowerCase())) {
        results.push(relativePath);
        if (results.length >= SEARCH_RESULTS_LIMIT) {
          break;
        }
      }
    }
  } catch (e) {
    // Ignore errors like permission denied
  }
  return results;
}