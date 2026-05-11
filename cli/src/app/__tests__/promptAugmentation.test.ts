import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { promises as fs } from 'fs';
import { augmentPromptWithFiles } from '@lib/prompt-augmentation.js';

const mockReadFile = vi.spyOn(fs, 'readFile');

describe('augmentPromptWithFiles', () => {
  beforeEach(() => {
    mockReadFile.mockReset();
  });

  afterEach(() => {
    vi.resetAllMocks();
  });

  it('returns original prompt when no @file references', async () => {
    const input = 'please help me with this code';
    const result = await augmentPromptWithFiles(input);
    expect(result).toBe(input);
    expect(mockReadFile).not.toHaveBeenCalled();
  });

  it('augments prompt with single file content', async () => {
    mockReadFile.mockResolvedValueOnce('console.log("hello");');

    const input = 'please refactor @src/index.ts';
    const result = await augmentPromptWithFiles(input);

    expect(mockReadFile).toHaveBeenCalledWith(
      expect.stringContaining('src/index.ts'),
      'utf-8'
    );
    expect(result).toContain('Content from src/index.ts:');
    expect(result).toContain('console.log("hello");');
    expect(result).toContain('User request: please refactor @src/index.ts');
  });

  it('handles multiple file references', async () => {
    mockReadFile
      .mockResolvedValueOnce('// config file')
      .mockResolvedValueOnce('// utils file');

    const input = 'compare @config.js and @utils.js';
    const result = await augmentPromptWithFiles(input);

    expect(mockReadFile).toHaveBeenCalledTimes(2);
    expect(result).toContain('Content from config.js:');
    expect(result).toContain('Content from utils.js:');
    expect(result).toContain('// config file');
    expect(result).toContain('// utils file');
  });

  it('handles file read errors gracefully', async () => {
    mockReadFile.mockRejectedValueOnce(new Error('File not found'));

    const input = 'please check @missing.ts';
    const result = await augmentPromptWithFiles(input);

    expect(result).toContain('Could not read file missing.ts');
    expect(result).toContain('Error: File not found');
    expect(result).toContain('User request: please check @missing.ts');
  });

  it('ignores @references inside backticks', async () => {
    const input = 'use `@file` syntax to reference files';
    const result = await augmentPromptWithFiles(input);

    expect(result).toBe(input);
    expect(mockReadFile).not.toHaveBeenCalled();
  });

  it('ignores @references that are part of words', async () => {
    const input = 'email me at user@file.com';
    const result = await augmentPromptWithFiles(input);

    expect(result).toBe(input);
    expect(mockReadFile).not.toHaveBeenCalled();
  });
});