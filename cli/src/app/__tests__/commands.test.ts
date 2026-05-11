import { describe, it, expect } from 'vitest';
import { slashCommands } from '../commands.js';

describe('slashCommands', () => {
  it('has valid shapes for each command', () => {
    expect(Array.isArray(slashCommands)).toBe(true);
    for (const cmd of slashCommands) {
      expect(typeof cmd.name).toBe('string');
      expect(cmd.name.length).toBeGreaterThan(0);
      expect(typeof cmd.description).toBe('string');
      if (cmd.aliases) {
        expect(Array.isArray(cmd.aliases)).toBe(true);
        for (const a of cmd.aliases) {
          expect(typeof a).toBe('string');
          expect(a.length).toBeGreaterThan(0);
        }
      }
    }
  });

  it('enforces uniqueness of names and aliases; no alias duplicates or name collisions', () => {
    const names = new Set<string>();
    const aliases = new Set<string>();

    for (const cmd of slashCommands) {
      expect(names.has(cmd.name)).toBe(false);
      names.add(cmd.name);
      if (cmd.aliases) {
        for (const a of cmd.aliases) {
          expect(aliases.has(a)).toBe(false);
          expect(names.has(a)).toBe(false); // alias must not shadow a name
          aliases.add(a);
        }
      }
    }
  });

  it('includes expected common aliases for clear and quit', () => {
    const byName = Object.fromEntries(slashCommands.map(c => [c.name, c]));
    expect(byName.clear?.aliases).toContain('new');
    expect(byName.quit?.aliases).toContain('exit');
  });
});

