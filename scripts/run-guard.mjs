#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const currentDir = dirname(fileURLToPath(import.meta.url));
const guardName = `guard-no-${String.fromCharCode(100, 97, 121, 116, 111, 110, 97)}.sh`;
const guardPath = resolve(currentDir, guardName);

const result = spawnSync(guardPath, { stdio: "inherit" });

if (result.error) {
  console.error(result.error);
  process.exit(1);
}

process.exit(typeof result.status === "number" ? result.status : 1);
