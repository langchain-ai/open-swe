import { describe, expect, test } from "bun:test"
import { mkdtemp, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import {
  commandEnvironment,
  LocalExecutor,
  OUTPUT_KEEP_BYTES,
  OUTPUT_LIMIT_BYTES,
  OutputWindow,
} from "../src/executor"

async function workspace(): Promise<string> {
  return await mkdtemp(join(tmpdir(), "open-swe-bridge-"))
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
      OPEN_SWE_SESSION: "jwt",
      PAGER: "less",
    })
    expect(env["PATH"]).toBe("/usr/bin")
    expect(env["ANTHROPIC_API_KEY"]).toBeUndefined()
    expect(env["SLACK_BOT_TOKEN"]).toBeUndefined()
    expect(env["DB_PASSWORD"]).toBeUndefined()
    expect(env["SOME_SECRET"]).toBeUndefined()
    expect(env["OPEN_SWE_SESSION"]).toBeUndefined()
    expect(env["GITHUB_TOKEN"]).toBe("gho_x")
    expect(env["GH_TOKEN"]).toBe("gho_y")
    expect(env["PAGER"]).toBe("cat")
    expect(env["GIT_PAGER"]).toBe("cat")
    expect(env["CI"]).toBe("1")
    expect(env["GIT_TERMINAL_PROMPT"]).toBe("0")
  })
})

function captured(chunks: readonly Uint8Array[]): OutputWindow {
  const output = new OutputWindow()
  for (const chunk of chunks) output.push(chunk)
  return output
}

describe("OutputWindow", () => {
  test("keeps short output verbatim, across chunks", () => {
    const encoder = new TextEncoder()
    expect(
      captured([encoder.encode("hel"), encoder.encode("lo")]).finish()
    ).toEqual({ output: "hello", truncated: false })
  })

  test("keeps the head and tail of oversized output streamed in small chunks", () => {
    const size = OUTPUT_LIMIT_BYTES + 1024
    const bytes = new Uint8Array(size).fill(0x61)
    bytes[0] = 0x48
    bytes[size - 1] = 0x5a
    const chunks: Uint8Array[] = []
    for (let offset = 0; offset < size; offset += 4096)
      chunks.push(bytes.subarray(offset, offset + 4096))
    const { output, truncated } = captured(chunks).finish()
    expect(truncated).toBe(true)
    expect(output.startsWith("H")).toBe(true)
    expect(output.endsWith("Z")).toBe(true)
    expect(output).toContain(`[omitted 1024 bytes of output]`)
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

  test("runs in its working directory", async () => {
    const root = await workspace()
    await writeFile(join(root, "marker.txt"), "x")
    const result = await new LocalExecutor(root).execute("ls", null)
    expect(result.output.trim()).toBe("marker.txt")
  })

  test("times out with exit code 124 and a marker", async () => {
    const executor = new LocalExecutor(await workspace())
    const result = await executor.execute("sleep 5", 1)
    expect(result.exit_code).toBe(124)
    expect(result.output).toContain("[command timed out after 1s]")
  })

  test("stops a running command on request", async () => {
    const executor = new LocalExecutor(await workspace())
    const started = Date.now()
    const running = executor.execute("sleep 30", 300)
    await new Promise((done) => setTimeout(done, 200))
    executor.stopAll()
    const result = await running
    expect(Date.now() - started).toBeLessThan(5_000)
    expect(result.output).toContain("[command stopped")
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
