import { describe, expect, test } from "bun:test"
import { mkdtemp, readFile, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import {
  commandEnvironment,
  LocalExecutor,
  OUTPUT_KEEP_BYTES,
  OUTPUT_LIMIT_BYTES,
  truncateOutput,
} from "../src/executor.ts"

async function workspace(): Promise<string> {
  return await mkdtemp(join(tmpdir(), "open-swe-cli-"))
}

describe("commandEnvironment", () => {
  test("strips secret-shaped keys but keeps the GitHub tokens", () => {
    const env = commandEnvironment({
      PATH: "/usr/bin",
      ANTHROPIC_API_KEY: "secret",
      SLACK_BOT_TOKEN: "secret",
      DB_PASSWORD: "secret",
      SOME_SECRET: "secret",
      GITHUB_TOKEN: "gho_x",
      GH_TOKEN: "gho_y",
      PAGER: "less",
    })
    expect(env["PATH"]).toBe("/usr/bin")
    expect(env["ANTHROPIC_API_KEY"]).toBeUndefined()
    expect(env["SLACK_BOT_TOKEN"]).toBeUndefined()
    expect(env["DB_PASSWORD"]).toBeUndefined()
    expect(env["SOME_SECRET"]).toBeUndefined()
    expect(env["GITHUB_TOKEN"]).toBe("gho_x")
    expect(env["GH_TOKEN"]).toBe("gho_y")
    expect(env["PAGER"]).toBe("cat")
    expect(env["GIT_PAGER"]).toBe("cat")
    expect(env["CI"]).toBe("1")
    expect(env["GIT_TERMINAL_PROMPT"]).toBe("0")
  })
})

describe("truncateOutput", () => {
  test("keeps short output verbatim", () => {
    const bytes = new TextEncoder().encode("hello")
    expect(truncateOutput(bytes)).toEqual({ output: "hello", truncated: false })
  })

  test("keeps the head and tail of oversized output", () => {
    const size = OUTPUT_LIMIT_BYTES + 1024
    const bytes = new Uint8Array(size).fill(0x61)
    bytes[0] = 0x48
    bytes[size - 1] = 0x5a
    const { output, truncated } = truncateOutput(bytes)
    expect(truncated).toBe(true)
    expect(output.startsWith("H")).toBe(true)
    expect(output.endsWith("Z")).toBe(true)
    expect(output).toContain(`[oswe: omitted 1024 bytes of output]`)
    expect(output.length).toBeLessThan(OUTPUT_KEEP_BYTES * 2 + 200)
  })
})

describe("execute", () => {
  test("interleaves stdout and stderr and reports the exit code", async () => {
    const executor = new LocalExecutor(await workspace())
    const result = await executor.execute(
      "echo out; echo err 1>&2; exit 3",
      null
    )
    expect(result.exit_code).toBe(3)
    expect(result.truncated).toBe(false)
    expect(result.output).toContain("out")
    expect(result.output).toContain("err")
  })

  test("runs in the bridge root", async () => {
    const root = await workspace()
    await writeFile(join(root, "marker.txt"), "x")
    const result = await new LocalExecutor(root).execute("ls", null)
    expect(result.output.trim()).toBe("marker.txt")
  })

  test("times out with exit code 124 and a marker", async () => {
    const executor = new LocalExecutor(await workspace())
    const result = await executor.execute("sleep 5", 1)
    expect(result.exit_code).toBe(124)
    expect(result.output).toContain("[oswe: command timed out after 1s]")
  })

  test("truncates output beyond the cap", async () => {
    const executor = new LocalExecutor(await workspace())
    const result = await executor.execute(
      `head -c ${OUTPUT_LIMIT_BYTES + 4096} /dev/zero | tr '\\0' 'a'`,
      30
    )
    expect(result.truncated).toBe(true)
    expect(result.output).toContain("omitted")
  })

  test("strips secrets from the child environment", async () => {
    const executor = new LocalExecutor(await workspace())
    process.env["OPEN_SWE_CLI_TEST_API_KEY"] = "leaked"
    try {
      const result = await executor.execute(
        'echo "[${OPEN_SWE_CLI_TEST_API_KEY:-absent}]"',
        30
      )
      expect(result.output).toContain("[absent]")
    } finally {
      delete process.env["OPEN_SWE_CLI_TEST_API_KEY"]
    }
  })
})

describe("upload_files", () => {
  test("writes bytes and creates parent directories", async () => {
    const root = await workspace()
    const executor = new LocalExecutor(root)
    const responses = await executor.uploadFiles([
      { path: "nested/deep/file.txt", content_base64: btoa("written") },
    ])
    expect(responses).toEqual([{ path: "nested/deep/file.txt", error: null }])
    expect(await readFile(join(root, "nested/deep/file.txt"), "utf8")).toBe(
      "written"
    )
  })

  test("reports invalid paths and undecodable content", async () => {
    const executor = new LocalExecutor(await workspace())
    const responses = await executor.uploadFiles([
      { path: "", content_base64: "" },
      { path: "bad.txt", content_base64: "not base64 ***" },
    ])
    expect(responses[0]?.error).toBe("invalid_path")
    expect(responses[1]?.error).toBe("invalid base64 content")
  })

  test("reports permission_denied for an unwritable directory", async () => {
    const root = await workspace()
    const locked = join(root, "locked")
    await Bun.$`mkdir -p ${locked}`.quiet()
    await Bun.$`chmod 500 ${locked}`.quiet()
    try {
      const executor = new LocalExecutor(root)
      const responses = await executor.uploadFiles([
        { path: "locked/denied.txt", content_base64: btoa("x") },
      ])
      expect(responses[0]?.error).toBe("permission_denied")
    } finally {
      await Bun.$`chmod 700 ${locked}`.quiet()
    }
  })
})

describe("download_files", () => {
  test("returns base64 content for readable files", async () => {
    const root = await workspace()
    await writeFile(join(root, "read.txt"), "contents")
    const executor = new LocalExecutor(root)
    const responses = await executor.downloadFiles(["read.txt"])
    expect(responses[0]?.error).toBeNull()
    expect(atob(responses[0]?.content_base64 ?? "")).toBe("contents")
  })

  test("maps missing files and directories to contract errors", async () => {
    const root = await workspace()
    await Bun.$`mkdir -p ${join(root, "adir")}`.quiet()
    const executor = new LocalExecutor(root)
    const responses = await executor.downloadFiles(["missing.txt", "adir"])
    expect(responses[0]?.error).toBe("file_not_found")
    expect(responses[0]?.content_base64).toBeNull()
    expect(responses[1]?.error).toBe("is_a_directory")
  })
})

describe("dispatch", () => {
  test("rejects unknown methods", async () => {
    const executor = new LocalExecutor(await workspace())
    expect(await executor.dispatch("teleport", {})).toEqual({
      error: "unsupported method teleport",
    })
  })

  test("wraps execute results in the wire shape", async () => {
    const executor = new LocalExecutor(await workspace())
    const outcome = await executor.dispatch("execute", { command: "echo hi" })
    expect(outcome).toHaveProperty("result")
    if (!("result" in outcome)) throw new Error("expected a result")
    expect(outcome.result["exit_code"]).toBe(0)
    expect(outcome.result["truncated"]).toBe(false)
    expect(String(outcome.result["output"]).trim()).toBe("hi")
  })

  test("rejects malformed upload and download params", async () => {
    const executor = new LocalExecutor(await workspace())
    expect(await executor.dispatch("upload_files", { files: [{}] })).toEqual({
      error: "upload_files requires files with path and content_base64",
    })
    expect(await executor.dispatch("download_files", {})).toEqual({
      error: "download_files requires a paths array",
    })
  })
})
