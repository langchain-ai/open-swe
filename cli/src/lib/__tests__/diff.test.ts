import { describe, it, expect } from 'vitest';
import { buildDiffLines, applyPatch } from '../diff.js';

describe('buildDiffLines', () => {
  it('should handle additions, removals, and context correctly', () => {
    const original = [
      'line 1',
      'line 2',
      'line 3',
      'line 5',
    ];
    const updated = [
      'line 1',
      'line 3',
      'line 4',
      'line 5',
    ];

    const diff = buildDiffLines(original, updated);
    expect(diff).toEqual([
      { type: 'context', oldLine: 1, newLine: 1, text: 'line 1' },
      { type: 'remove', oldLine: 2, text: 'line 2' },
      { type: 'context', oldLine: 3, newLine: 2, text: 'line 3' },
      { type: 'add', newLine: 3, text: 'line 4' },
      { type: 'context', oldLine: 4, newLine: 4, text: 'line 5' },
    ]);
  });

  it('should handle empty original (all additions)', () => {
    const original: string[] = [];
    const updated = ['first line', 'second line'];
    const diff = buildDiffLines(original, updated);
    expect(diff).toEqual([
      { type: 'add', newLine: 1, text: 'first line' },
      { type: 'add', newLine: 2, text: 'second line' },
    ]);
  });

  it('should handle empty updated (all removals)', () => {
    const original = ['first line', 'second line'];
    const updated: string[] = [];
    const diff = buildDiffLines(original, updated);
    expect(diff).toEqual([
      { type: 'remove', oldLine: 1, text: 'first line' },
      { type: 'remove', oldLine: 2, text: 'second line' },
    ]);
  });

  it('should handle identical arrays', () => {
    const original = ['line 1', 'line 2', 'line 3'];
    const updated = ['line 1', 'line 2', 'line 3'];
    const diff = buildDiffLines(original, updated);
    expect(diff).toEqual([
      { type: 'context', oldLine: 1, newLine: 1, text: 'line 1' },
      { type: 'context', oldLine: 2, newLine: 2, text: 'line 2' },
      { type: 'context', oldLine: 3, newLine: 3, text: 'line 3' },
    ]);
  });

  it('should handle both arrays empty', () => {
    const original: string[] = [];
    const updated: string[] = [];
    const diff = buildDiffLines(original, updated);
    expect(diff).toEqual([]);
  });
});

describe('applyPatch', () => {
    it('should apply a simple patch', () => {
      const original = 'line 1\nline 2\nline 3';
      const patch = `--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 line 1
-line 2
+line two
 line 3`;
      const { newContent } = applyPatch(original, patch);
      expect(newContent).toBe('line 1\nline two\nline 3');
    });

    it('should handle additions', () => {
        const original = 'line 1\nline 3';
        const patch = `--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,3 @@
 line 1
+line 2
 line 3`;
        const { newContent } = applyPatch(original, patch);
        expect(newContent).toBe('line 1\nline 2\nline 3');
    });

    it('should handle removals', () => {
        const original = 'line 1\nline 2\nline 3';
        const patch = `--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,2 @@
 line 1
-line 2
 line 3`;
        const { newContent } = applyPatch(original, patch);
        expect(newContent).toBe('line 1\nline 3');
    });

    it('should apply patch to create a new file', () => {
        const original = '';
        const patch = `--- /dev/null
+++ b/new_file.txt
@@ -0,0 +1,2 @@
+Hello
+World`;
        const { newContent } = applyPatch(original, patch);
        expect(newContent).toBe('Hello\nWorld');
    });

    it('should handle multiple hunks', () => {
        const original = 'one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten';
        const patch = `--- a/file.txt
+++ b/file.txt
@@ -1,4 +1,4 @@
 one
-two
-three
+2
+3
 four
@@ -7,4 +7,4 @@
 seven
-eight
-nine
+8
+9
 ten`;
        const { newContent } = applyPatch(original, patch);
        expect(newContent).toBe('one\n2\n3\nfour\nfive\nsix\nseven\n8\n9\nten');
    });
});

