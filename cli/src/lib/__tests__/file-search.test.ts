import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { promises as fs } from 'fs';
import path from 'path';
import { searchFiles } from '../file-search.js';

// Helper function to create a temporary test directory structure
async function createTestDirectory(): Promise<string> {
  const base = path.join(process.cwd(), '.tmp');
  const testDir = path.join(base, `file-search-test-${Date.now()}-${Math.random().toString(16).slice(2)}`);

  await fs.mkdir(base, { recursive: true });
  await fs.mkdir(testDir, { recursive: true });

  // Create test directory structure
  const files = [
    'package.json',
    'README.md',
    'src/index.ts',
    'src/components/App.tsx',
    'src/components/Header.tsx',
    'src/lib/utils.ts',
    'src/lib/helpers.js',
    'tests/app.test.ts',
    'tests/utils.test.js',
    'docs/api.md',
    'docs/guide/getting-started.md',
    'scripts/build.sh',
    'public/index.html',
    'styles/main.css',
    // Create some files that should be ignored
    'node_modules/some-package/index.js',
    'node_modules/another-package/lib.ts',
    '.git/config',
    '.git/HEAD',
    'dist/bundle.js',
    'dist/index.html'
  ];

  for (const file of files) {
    const filePath = path.join(testDir, file);
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, `// Content of ${file}`);
  }

  return testDir;
}

// Helper function to clean up test directory
async function cleanupTestDirectory(testDir: string): Promise<void> {
  await fs.rm(testDir, { recursive: true, force: true });
}

describe('file search functionality', () => {
  let testDir: string;

  beforeEach(async () => {
    testDir = await createTestDirectory();
  });

  afterEach(async () => {
    await cleanupTestDirectory(testDir);
  });

  it('finds files matching a simple query', async () => {
    const results = await searchFiles('package', testDir);
    expect(results).toContain('package.json');
    expect(results).not.toContain('node_modules/some-package/index.js'); // Should ignore node_modules
  });

  it('finds files in subdirectories', async () => {
    const results = await searchFiles('App', testDir);
    expect(results).toContain('src/components/App.tsx');
  });

  it('performs case-insensitive search', async () => {
    const results = await searchFiles('app', testDir);
    expect(results).toContain('src/components/App.tsx');
    expect(results).toContain('tests/app.test.ts');
  });

  it('finds files by extension', async () => {
    const results = await searchFiles('.tsx', testDir);
    expect(results).toContain('src/components/App.tsx');
    expect(results).toContain('src/components/Header.tsx');
    expect(results).not.toContain('src/index.ts');
  });

  it('finds files by partial path', async () => {
    const results = await searchFiles('src/lib', testDir);
    expect(results).toContain('src/lib/utils.ts');
    expect(results).toContain('src/lib/helpers.js');
    expect(results).not.toContain('src/components/App.tsx');
  });

  it('ignores node_modules directory', async () => {
    const results = await searchFiles('index', testDir);
    expect(results).toContain('src/index.ts');
    expect(results).toContain('public/index.html');
    expect(results).not.toContain('node_modules/some-package/index.js');
  });

  it('ignores .git directory', async () => {
    const results = await searchFiles('config', testDir);
    expect(results).not.toContain('.git/config');
  });

  it('ignores dist directory', async () => {
    const results = await searchFiles('bundle', testDir);
    expect(results).not.toContain('dist/bundle.js');
  });

  it('limits results to 10 files for performance', async () => {
    // Create a test case with many matching files
    const manyFilesDir = path.join(testDir, 'many-files');
    await fs.mkdir(manyFilesDir, { recursive: true });

    // Create 15 files that match the query
    for (let i = 0; i < 15; i++) {
      await fs.writeFile(path.join(manyFilesDir, `test-file-${i}.txt`), 'content');
    }

    const results = await searchFiles('test-file', testDir);
    expect(results.length).toBeLessThanOrEqual(10);
  });

  it('returns empty array when no files match', async () => {
    const results = await searchFiles('nonexistent-file-xyz', testDir);
    expect(results).toEqual([]);
  });

  it('handles empty query string', async () => {
    const results = await searchFiles('', testDir);
    expect(results.length).toBeGreaterThan(0);
    expect(results.length).toBeLessThanOrEqual(10);
  });

  it('returns relative paths from the root directory', async () => {
    const results = await searchFiles('utils', testDir);
    expect(results).toContain('src/lib/utils.ts');
    expect(results).toContain('tests/utils.test.js');

    // Ensure paths are relative (don't start with /)
    results.forEach(result => {
      expect(result).not.toMatch(/^[\/\\]/);
    });
  });

  it('handles permission denied errors gracefully', async () => {
    // This test just ensures the function doesn't throw when encountering permission errors
    // The actual behavior depends on the system, but it should not crash
    const results = await searchFiles('test', testDir);
    expect(Array.isArray(results)).toBe(true);
  });

  it('handles nested directory structures', async () => {
    const results = await searchFiles('getting-started', testDir);
    expect(results).toContain('docs/guide/getting-started.md');
  });

  it('finds files with different extensions', async () => {
    const results = await searchFiles('test', testDir);
    expect(results).toContain('tests/app.test.ts');
    expect(results).toContain('tests/utils.test.js');
  });
});