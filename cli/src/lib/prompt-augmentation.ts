import { promises as fs } from 'fs';
import path from 'path';

/**
 * Replaces @file references with an augmented prompt that includes file contents.
 * Example: "please refactor @src/index.ts" becomes:
 *  "Content from src/index.ts: --- <file> ---\n\nUser request: <original>"
 */
export async function augmentPromptWithFiles(value: string): Promise<string> {
  let finalPrompt = value;
  const fileRegex = /(?<![\w`])@(\S+)/g;
  const matches = [...value.matchAll(fileRegex as any)];

  if (matches.length === 0) return finalPrompt;

  const augmented: string[] = [];
  const filesToRead = matches.map((match) => {
    const alias = match[0];
    const rel = match[1];
    const abs = path.resolve(process.cwd(), rel);
    return { alias, relativePath: rel, filePath: abs };
  });

  for (const f of filesToRead) {
    try {
      const content = await fs.readFile(f.filePath, 'utf-8');
      augmented.push(`Content from ${f.relativePath}:\n---\n${content}\n---`);
    } catch (e: any) {
      augmented.push(
        `Could not read file ${f.relativePath}. Error: ${e?.message ?? String(e)}`
      );
    }
  }
  finalPrompt = `${augmented.join('\n\n')}\n\nUser request: ${value}`;
  return finalPrompt;
}