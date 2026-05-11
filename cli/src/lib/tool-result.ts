import type { Result } from '@types';

export function createSuccessResult<T>(data: T): string {
  const result: Result<T> = { ok: true, data };
  return JSON.stringify(result);
}

export function createErrorResult(error: string): string {
  const result: Result<never> = { ok: false, error };
  return JSON.stringify(result);
}

export function createSimpleSuccessResult(message: string): string {
  return createSuccessResult({ message });
}

export function createFileOperationResult(summary: string, diffLines?: any[]): string {
  return createSuccessResult({ summary, diffLines: diffLines || [] });
}