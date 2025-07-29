import { describe, it, expect } from "@jest/globals";
import {
  shouldExcludeFile,
  parseGitStatusOutput,
} from "../utils/github/git.js";
import { DEFAULT_EXCLUDED_PATTERNS } from "../utils/github/constants.js";

describe("Git File Validation", () => {
  describe("Realistic git status scenarios", () => {
    it("should handle typical development workspace changes", () => {
      const gitStatusOutput = ` M apps/open-swe/src/utils/github/git.ts
?? apps/open-swe/src/__tests__/git-file-validation.test.ts
 M package.json
?? node_modules/.cache/package-lock.json
 D old-config.json
?? dist/bundle.js
?? .env.local
?? logs/error.log
?? .DS_Store
 M README.md
?? temp-backup.txt`;

      const allFiles = parseGitStatusOutput(gitStatusOutput);
      const validFiles = allFiles.filter(
        (file) => !shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );
      const excludedFiles = allFiles.filter((file) =>
        shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );

      expect(validFiles).toEqual([
        "apps/open-swe/src/utils/github/git.ts",
        "apps/open-swe/src/__tests__/git-file-validation.test.ts",
        "package.json",
        "old-config.json",
        "README.md",
        "temp-backup.txt",
      ]);

      expect(excludedFiles).toEqual([
        "node_modules/.cache/package-lock.json",
        "dist/bundle.js",
        ".env.local",
        "logs/error.log",
        ".DS_Store",
      ]);
    });

    it("should handle file moves and renames", () => {
      // Git status with file moves (R) and renames
      const gitStatusOutput = `R  src/old-file.ts -> src/new-file.ts
 M src/components/Button.tsx
?? node_modules/react/index.js
?? dist/assets/main.css
?? .env.production
?? logs/debug.log
 M package.json
?? .DS_Store`;

      const allFiles = parseGitStatusOutput(gitStatusOutput);
      const validFiles = allFiles.filter(
        (file) => !shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );
      const excludedFiles = allFiles.filter((file) =>
        shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );

      expect(validFiles).toEqual([
        "src/old-file.ts -> src/new-file.ts",
        "src/components/Button.tsx",
        "package.json",
      ]);

      expect(excludedFiles).toEqual([
        "node_modules/react/index.js",
        "dist/assets/main.css",
        ".env.production",
        "logs/debug.log",
        ".DS_Store",
      ]);
    });

    it("should handle nested directory structures", () => {
      // Complex nested directory structure
      const gitStatusOutput = ` M apps/web/src/components/ui/button.tsx
?? apps/web/node_modules/react/index.js
?? apps/web/dist/assets/main.css
?? apps/open-swe/src/langgraph_api/server.py
?? apps/open-swe/.env.development
?? packages/shared/src/utils.ts
?? .turbo/cache/file
?? coverage/lcov.info
?? logs/app.log
?? .DS_Store`;

      const allFiles = parseGitStatusOutput(gitStatusOutput);
      const validFiles = allFiles.filter(
        (file) => !shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );
      const excludedFiles = allFiles.filter((file) =>
        shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );

      expect(validFiles).toEqual([
        "apps/web/src/components/ui/button.tsx",
        "packages/shared/src/utils.ts",
      ]);

      expect(excludedFiles).toEqual([
        "apps/web/node_modules/react/index.js",
        "apps/web/dist/assets/main.css",
        "apps/open-swe/src/langgraph_api/server.py",
        "apps/open-swe/.env.development",
        ".turbo/cache/file",
        "coverage/lcov.info",
        "logs/app.log",
        ".DS_Store",
      ]);
    });

    it("should handle Windows-style paths", () => {
      // Git status with Windows backslashes
      const gitStatusOutput = ` M src\\components\\Button.tsx
?? node_modules\\react\\index.js
?? dist\\bundle.js
?? .env.local
?? logs\\error.log
 M package.json
?? .DS_Store`;

      const allFiles = parseGitStatusOutput(gitStatusOutput);
      const validFiles = allFiles.filter(
        (file) => !shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );
      const excludedFiles = allFiles.filter((file) =>
        shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );

      expect(validFiles).toEqual([
        "src\\components\\Button.tsx",
        "package.json",
      ]);

      expect(excludedFiles).toEqual([
        "node_modules\\react\\index.js",
        "dist\\bundle.js",
        ".env.local",
        "logs\\error.log",
        ".DS_Store",
      ]);
    });

    it("should handle empty git status", () => {
      const gitStatusOutput = "";

      const allFiles = parseGitStatusOutput(gitStatusOutput);
      const validFiles = allFiles.filter(
        (file) => !shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );
      const excludedFiles = allFiles.filter((file) =>
        shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );

      expect(allFiles).toEqual([]);
      expect(validFiles).toEqual([]);
      expect(excludedFiles).toEqual([]);
    });

    it("should handle git status with only whitespace and empty lines", () => {
      const gitStatusOutput = `
  
  `;

      const allFiles = parseGitStatusOutput(gitStatusOutput);
      const validFiles = allFiles.filter(
        (file) => !shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );
      const excludedFiles = allFiles.filter((file) =>
        shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );

      expect(allFiles).toEqual([]);
      expect(validFiles).toEqual([]);
      expect(excludedFiles).toEqual([]);
    });
  });

  describe("All git status indicators", () => {
    it("should handle all possible git status indicators", () => {
      const gitStatusOutput = ` M modified-file.txt
M  staged-modified.txt
A  new-file.txt
 D deleted-file.txt
D  staged-deleted.txt
R  old-file.txt -> new-file.txt
C  copied-file.txt
U  unmerged-file.txt
?? untracked-file.txt
!! ignored-file.txt
 T type-changed.txt
T  staged-type-changed.txt`;

      const allFiles = parseGitStatusOutput(gitStatusOutput);
      expect(allFiles).toEqual([
        "modified-file.txt",
        "staged-modified.txt",
        "new-file.txt",
        "deleted-file.txt",
        "staged-deleted.txt",
        "old-file.txt -> new-file.txt",
        "copied-file.txt",
        "unmerged-file.txt",
        "untracked-file.txt",
        "ignored-file.txt",
        "type-changed.txt",
        "staged-type-changed.txt",
      ]);
    });
  });

  describe("File names with special characters", () => {
    it("should handle files with spaces in names", () => {
      const gitStatusOutput = ` M "file with spaces.txt"
?? "another file with spaces.md"
?? node_modules/"package with spaces"`;

      const allFiles = parseGitStatusOutput(gitStatusOutput);
      const validFiles = allFiles.filter(
        (file) => !shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );
      const excludedFiles = allFiles.filter((file) =>
        shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );

      expect(validFiles).toEqual([
        '"file with spaces.txt"',
        '"another file with spaces.md"',
      ]);

      expect(excludedFiles).toEqual(['node_modules/"package with spaces"']);
    });

    it("should handle files with special characters", () => {
      const gitStatusOutput = ` M file-with-dashes.txt
?? file_with_underscores.md
?? file.with.dots.js
?? file@symbol.com
?? file#hash.txt
?? file$dollar.txt
?? file%percent.txt
?? file^caret.txt
?? file&ampersand.txt
?? file*asterisk.txt
?? file(open).txt
?? file)close.txt
?? file[open].txt
?? file]close.txt
?? file{open}.txt
?? file}close.txt
?? file|pipe.txt
?? file\\backslash.txt
?? file"quote.txt
?? file'apostrophe.txt
?? file;semicolon.txt
?? file,comma.txt
?? file<less.txt
?? file>greater.txt
?? file=equals.txt
?? file+plus.txt
?? file~tilde.txt`;

      const allFiles = parseGitStatusOutput(gitStatusOutput);
      // All should be valid files (no exclusions)
      const validFiles = allFiles.filter(
        (file) => !shouldExcludeFile(file, DEFAULT_EXCLUDED_PATTERNS),
      );
      expect(validFiles).toEqual(allFiles);
    });
  });

  describe("Edge cases and security", () => {
    it("should handle patterns with regex metacharacters safely", () => {
      const dangerousPatterns = ["*.log[", "*.(log|txt)", "temp*", "*cache*"];
      const testFiles = [
        "error.log[",
        "test.(log|txt)",
        "temp.cache",
        "mycache.file",
      ];

      testFiles.forEach((file) => {
        const result = shouldExcludeFile(file, dangerousPatterns);
        if (file === "error.log[") {
          expect(result).toBe(true);
        } else if (file === "test.(log|txt)") {
          expect(result).toBe(true);
        } else if (file === "temp.cache") {
          expect(result).toBe(true);
        } else if (file === "mycache.file") {
          expect(result).toBe(true);
        }
      });
    });

    it("should handle very long file paths", () => {
      const longPath = "a".repeat(1000) + "/very/deep/nested/path/to/file.ts";
      const result = shouldExcludeFile(longPath, DEFAULT_EXCLUDED_PATTERNS);
      expect(result).toBe(false);
    });

    it("should handle unicode characters in paths", () => {
      const unicodePath = "src/测试/文件.ts";
      const result = shouldExcludeFile(unicodePath, DEFAULT_EXCLUDED_PATTERNS);
      expect(result).toBe(false);
    });

    it("should handle empty and whitespace-only lines", () => {
      const gitStatusOutput = `
  
  `;
      const allFiles = parseGitStatusOutput(gitStatusOutput);
      expect(allFiles).toEqual([]);
    });

    it("should handle multiple consecutive spaces", () => {
      const gitStatusOutput = ` M    file-with-many-spaces.txt`;
      const allFiles = parseGitStatusOutput(gitStatusOutput);
      expect(allFiles).toEqual(["   file-with-many-spaces.txt"]);
    });

    it("should handle files with leading/trailing spaces", () => {
      const gitStatusOutput = ` M  " file-with-leading-space.txt"
 M  "file-with-trailing-space.txt "`;
      const allFiles = parseGitStatusOutput(gitStatusOutput);
      expect(allFiles).toEqual([
        ' " file-with-leading-space.txt"',
        ' "file-with-trailing-space.txt "',
      ]);
    });
  });
});
