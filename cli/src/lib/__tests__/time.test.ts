import { describe, it, expect, vi } from 'vitest';
import { nowTime } from '../time.js';

describe('time helpers', () => {
  it('nowTime delegates to toLocaleTimeString with hour/minute formatting', () => {
    const spy = vi.spyOn(Date.prototype as any, 'toLocaleTimeString').mockReturnValue('07:45');
    expect(nowTime()).toBe('07:45');
    spy.mockRestore();
  });
});

