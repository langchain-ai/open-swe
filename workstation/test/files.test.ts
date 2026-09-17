import assert from "node:assert/strict"
import {
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { after, before, describe, it } from "node:test"

import {
  downloadFiles,
  edit,
  ls,
  read,
  remove,
  uploadFiles,
  write,
} from "../src/backend/files.ts"

let base: string
let counter = 0

before(async () => {
  base = await mkdtemp(path.join(tmpdir(), "workstation-files-"))
})

after(async () => {
  await rm(base, { recursive: true, force: true })
})

/** Canonical, so a returned real path is directly comparable to `join(root, …)`. */
async function makeRoot(): Promise<string> {
  counter += 1
  const root = path.join(base, `root-${counter}`)
  await mkdir(root, { recursive: true })
  return realpath(root)
}

const TEN_LINES = Array.from({ length: 10 }, (_, i) => `line${i + 1}\n`).join(
  ""
)

async function rootWithTenLines(): Promise<string> {
  const root = await makeRoot()
  await writeFile(path.join(root, "lines.txt"), TEN_LINES)
  return root
}

describe("read", () => {
  it("returns a window in the middle of a file with full pagination", async () => {
    const root = await rootWithTenLines()

    const result = await read(root, "lines.txt", { offset: 3, limit: 4 })

    assert.equal(result.error, undefined)
    assert.deepEqual(result.fileData, {
      content: "line4\nline5\nline6\nline7\n",
      encoding: "utf-8",
    })
    assert.equal(result.totalLines, 10)
    assert.equal(result.startLine, 4)
    assert.equal(result.endLine, 7)
    assert.equal(result.nextOffset, 7)
    assert.equal(result.noLinesRequested, undefined)
  })

  it("omits nextOffset once the window reaches the last line", async () => {
    const root = await rootWithTenLines()

    const result = await read(root, "lines.txt", { offset: 8, limit: 4 })

    assert.equal(result.startLine, 9)
    assert.equal(result.endLine, 10)
    assert.equal(result.totalLines, 10)
    assert.equal(result.nextOffset, undefined)
  })

  it("reads the whole file up to the default limit", async () => {
    const root = await rootWithTenLines()

    const result = await read(root, "lines.txt")

    assert.equal(result.fileData?.content, TEN_LINES)
    assert.equal(result.startLine, 1)
    assert.equal(result.endLine, 10)
    assert.equal(result.nextOffset, undefined)
  })

  it("requests no lines for a non-positive limit", async () => {
    const root = await rootWithTenLines()

    for (const limit of [0, -3]) {
      const result = await read(root, "lines.txt", { limit })

      assert.equal(result.error, undefined)
      assert.deepEqual(result.fileData, { content: "", encoding: "utf-8" })
      assert.equal(result.noLinesRequested, true)
      assert.equal(result.totalLines, undefined)
      assert.equal(result.startLine, undefined)
      assert.equal(result.endLine, undefined)
      assert.equal(result.nextOffset, undefined)
    }
  })

  it("starts at the first line for a negative offset", async () => {
    const root = await rootWithTenLines()

    const result = await read(root, "lines.txt", { offset: -5, limit: 2 })

    assert.equal(result.fileData?.content, "line1\nline2\n")
    assert.equal(result.startLine, 1)
    assert.equal(result.endLine, 2)
    assert.equal(result.nextOffset, 2)
  })

  it("reports an offset past the end of the file", async () => {
    const root = await rootWithTenLines()

    const result = await read(root, "lines.txt", { offset: 20 })

    assert.equal(result.error, "Line offset 20 exceeds file length (10 lines)")
    assert.equal(result.fileData, undefined)
  })

  it("returns a binary file base64-encoded and unpaginated", async () => {
    const root = await makeRoot()
    const bytes = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x00, 0x0a, 0x0d, 0xff])
    await writeFile(path.join(root, "pixel.png"), bytes)

    const result = await read(root, "pixel.png")

    assert.equal(result.error, undefined)
    assert.equal(result.fileData?.encoding, "base64")
    assert.deepEqual(
      Array.from(Buffer.from(result.fileData?.content ?? "", "base64")),
      Array.from(bytes)
    )
    assert.equal(result.totalLines, undefined)
    assert.equal(result.startLine, undefined)
  })

  it("fails rather than substituting replacement characters for undecodable text", async () => {
    const root = await makeRoot()
    await writeFile(
      path.join(root, "notes.txt"),
      Buffer.from([0xff, 0xfe, 0x41])
    )

    const result = await read(root, "notes.txt")

    assert.equal(result.fileData, undefined)
    assert.match(result.error ?? "", /^Error: /)
  })

  it("returns the empty-content reminder for empty and whitespace-only files", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "empty.txt"), "")
    await writeFile(path.join(root, "blank.txt"), "  \n\t\n")

    for (const name of ["empty.txt", "blank.txt"]) {
      const result = await read(root, name)

      assert.equal(result.error, undefined)
      assert.deepEqual(result.fileData, {
        content: "System reminder: File exists but has empty contents",
        encoding: "utf-8",
      })
      assert.equal(result.totalLines, undefined)
      assert.equal(result.noLinesRequested, undefined)
    }
  })

  it("reports a missing file", async () => {
    const root = await makeRoot()

    const result = await read(root, "nope.txt")

    assert.equal(result.error, "File 'nope.txt' not found")
  })

  it("reads a real absolute path inside the root", async () => {
    const root = await rootWithTenLines()

    const result = await read(root, path.join(root, "lines.txt"), { limit: 1 })

    assert.equal(result.fileData?.content, "line1\n")
  })

  it("refuses an absolute path outside the root instead of re-anchoring it", async () => {
    const root = await makeRoot()

    const result = await read(root, "/etc/hosts")

    assert.equal(
      result.error,
      "Error: /etc/hosts is outside the workstation root"
    )
    assert.equal(result.fileData, undefined)
  })

  it("reports a directory as not found", async () => {
    const root = await makeRoot()
    await mkdir(path.join(root, "sub"))

    const result = await read(root, "sub")

    assert.equal(result.error, "File 'sub' not found")
  })
})

describe("write", () => {
  it("creates missing parent directories", async () => {
    const root = await makeRoot()

    const result = await write(root, "a/b/c/file.txt", "hello")

    assert.equal(result.error, undefined)
    assert.equal(result.path, path.join(root, "a/b/c/file.txt"))
    assert.equal(
      await readFile(path.join(root, "a/b/c/file.txt"), "utf8"),
      "hello"
    )
  })

  it("overwrites an existing file", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "a much longer original")

    const result = await write(root, "file.txt", "short")

    assert.equal(result.error, undefined)
    assert.equal(await readFile(path.join(root, "file.txt"), "utf8"), "short")
  })
})

describe("edit", () => {
  it("refuses a non-unique oldString and leaves the file untouched", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "x\nx\nx\n")

    const result = await edit(root, "file.txt", "x", "y")

    assert.equal(result.path, undefined)
    assert.equal(result.occurrences, undefined)
    assert.match(result.error ?? "", /appears 3 times in file/)
    assert.equal(
      await readFile(path.join(root, "file.txt"), "utf8"),
      "x\nx\nx\n"
    )
  })

  it("replaces every occurrence with replaceAll", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "x\nx\nx\n")

    const result = await edit(root, "file.txt", "x", "y", true)

    assert.equal(result.error, undefined)
    assert.equal(result.path, path.join(root, "file.txt"))
    assert.equal(result.occurrences, 3)
    assert.equal(
      await readFile(path.join(root, "file.txt"), "utf8"),
      "y\ny\ny\n"
    )
  })

  it("replaces a unique oldString", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "alpha\nbeta\n")

    const result = await edit(root, "file.txt", "beta", "gamma")

    assert.equal(result.occurrences, 1)
    assert.equal(
      await readFile(path.join(root, "file.txt"), "utf8"),
      "alpha\ngamma\n"
    )
  })

  it("keeps dollar sequences in newString literal", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "token\n")

    const result = await edit(root, "file.txt", "token", "$& $1 $$")

    assert.equal(result.occurrences, 1)
    assert.equal(
      await readFile(path.join(root, "file.txt"), "utf8"),
      "$& $1 $$\n"
    )
  })

  it("reports an oldString that is absent", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "alpha\n")

    const result = await edit(root, "file.txt", "zzz", "y")

    assert.equal(result.error, "Error: String not found in file: 'zzz'")
  })

  it("hints when oldString has a trailing newline the file lacks", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "last line")

    const result = await edit(root, "file.txt", "last line\n", "replaced\n")

    assert.match(result.error ?? "", /old_string ends with a newline/)
    assert.match(result.error ?? "", /Retry with the trailing newline removed/)
    assert.equal(
      await readFile(path.join(root, "file.txt"), "utf8"),
      "last line"
    )
  })

  it("hints that the newline-stripped oldString is also ambiguous", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "dup dup")

    const result = await edit(root, "file.txt", "dup\n", "other")

    assert.match(result.error ?? "", /would appear 2 times in the file/)
  })

  it("reports an occurrence when oldString equals newString", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "alpha\n")

    const result = await edit(root, "file.txt", "alpha", "alpha")

    assert.equal(result.error, undefined)
    assert.equal(result.occurrences, 1)
    assert.equal(await readFile(path.join(root, "file.txt"), "utf8"), "alpha\n")
  })

  it("reports a missing file", async () => {
    const root = await makeRoot()

    const result = await edit(root, "nope.txt", "a", "b")

    assert.equal(result.error, "Error: File 'nope.txt' not found")
  })
})

describe("remove", () => {
  it("deletes a directory tree recursively", async () => {
    const root = await makeRoot()
    await mkdir(path.join(root, "tree/inner/deeper"), { recursive: true })
    await writeFile(path.join(root, "tree/inner/deeper/file.txt"), "content")
    await writeFile(path.join(root, "tree/top.txt"), "content")

    const result = await remove(root, "tree")

    assert.equal(result.error, undefined)
    assert.equal(result.path, path.join(root, "tree"))
    assert.deepEqual((await ls(root, root)).entries, [])
  })

  it("deletes a single file", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "content")

    const result = await remove(root, "file.txt")

    assert.equal(result.error, undefined)
    assert.deepEqual((await ls(root, root)).entries, [])
  })

  it("reports a missing path", async () => {
    const root = await makeRoot()

    const result = await remove(root, "nope")

    assert.equal(result.error, "Error: 'nope' not found")
  })
})

describe("uploadFiles and downloadFiles", () => {
  it("round-trips bytes exactly through nested directories", async () => {
    const root = await makeRoot()
    const bytes = new Uint8Array(256)
    for (let i = 0; i < 256; i += 1) bytes[i] = i

    const uploads = await uploadFiles(root, [
      { path: "nested/deep/blob.bin", content: bytes },
    ])
    const downloads = await downloadFiles(root, ["nested/deep/blob.bin"])

    const blob = path.join(root, "nested/deep/blob.bin")
    assert.deepEqual(uploads, [{ path: blob }])
    assert.equal(downloads.length, 1)
    assert.equal(downloads[0]?.error, undefined)
    assert.equal(downloads[0]?.path, blob)
    assert.deepEqual(Array.from(downloads[0]?.content ?? []), Array.from(bytes))
  })

  it("reports per-file failures without failing the batch", async () => {
    const root = await makeRoot()
    await mkdir(path.join(root, "dir"))
    await writeFile(path.join(root, "dir/file.txt"), "ok")

    const downloads = await downloadFiles(root, [
      "dir/file.txt",
      "dir",
      "missing.txt",
    ])

    assert.deepEqual(
      downloads.map((response) => response.error),
      [undefined, "is_directory", "file_not_found"]
    )
    assert.equal(downloads[0]?.content?.toString(), "ok")
  })
})

describe("ls", () => {
  it("lists direct children with type, size, and timestamp", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "12345")
    await mkdir(path.join(root, "sub"))
    await writeFile(path.join(root, "sub/inner.txt"), "nested")

    const result = await ls(root, root)

    assert.equal(result.error, undefined)
    assert.deepEqual(
      result.entries?.map((entry) => entry.path),
      [path.join(root, "file.txt"), `${path.join(root, "sub")}/`]
    )
    const file = result.entries?.find((entry) => !entry.isDir)
    assert.equal(file?.size, 5)
    assert.ok(!Number.isNaN(Date.parse(file?.modifiedAt ?? "")))
    const dir = result.entries?.find((entry) => entry.isDir)
    assert.equal(dir?.size, 0)
  })

  it("reports entries of a nested directory by their real path", async () => {
    const root = await makeRoot()
    await mkdir(path.join(root, "sub"))
    await writeFile(path.join(root, "sub/inner.txt"), "nested")

    const result = await ls(root, "sub")

    assert.deepEqual(
      result.entries?.map((entry) => entry.path),
      [path.join(root, "sub/inner.txt")]
    )
    assert.ok(path.isAbsolute(result.entries?.[0]?.path ?? ""))
  })

  it("returns an empty listing for an empty directory", async () => {
    const root = await makeRoot()

    const result = await ls(root, root)

    assert.equal(result.error, undefined)
    assert.deepEqual(result.entries, [])
  })

  it("skips a symlink that points out of the root", async () => {
    const root = await makeRoot()
    const outside = `${root}-outside`
    await mkdir(outside, { recursive: true })
    await writeFile(path.join(outside, "secret.txt"), "secret")
    await symlink(outside, path.join(root, "escape"))
    await writeFile(path.join(root, "file.txt"), "ok")

    const result = await ls(root, root)

    assert.equal(result.error, undefined)
    assert.deepEqual(
      result.entries?.map((entry) => entry.path),
      [path.join(root, "file.txt")]
    )
  })

  it("lists an in-root symlink under its own path", async () => {
    const root = await makeRoot()
    await mkdir(path.join(root, "target"))
    await symlink(path.join(root, "target"), path.join(root, "link"))

    const result = await ls(root, root)

    assert.equal(result.error, undefined)
    assert.deepEqual(
      result.entries?.map((entry) => entry.path),
      [`${path.join(root, "link")}/`, `${path.join(root, "target")}/`]
    )
  })

  it("reports a missing path", async () => {
    const root = await makeRoot()

    const result = await ls(root, "nope")

    assert.equal(result.error, "Path 'nope': path_not_found")
    assert.equal(result.entries, undefined)
  })

  it("reports a file as not a directory", async () => {
    const root = await makeRoot()
    await writeFile(path.join(root, "file.txt"), "content")

    const result = await ls(root, "file.txt")

    assert.equal(result.error, "Path 'file.txt': not_a_directory")
    assert.equal(result.entries, undefined)
  })
})

describe("root containment", () => {
  async function makeEscapeFixture(): Promise<{
    readonly root: string
    readonly outsideFile: string
    readonly escapes: readonly string[]
  }> {
    const root = await makeRoot()
    const outside = `${root}-outside`
    await mkdir(outside, { recursive: true })
    const outsideFile = path.join(outside, "secret.txt")
    await writeFile(outsideFile, "secret")
    await symlink(outside, path.join(root, "escape"))
    return {
      root,
      outsideFile,
      escapes: [`../${path.basename(outside)}/secret.txt`, "escape/secret.txt"],
    }
  }

  it("refuses ls, read, write, edit, and remove", async () => {
    const { root, outsideFile, escapes } = await makeEscapeFixture()

    for (const escape of escapes) {
      for (const error of [
        (await ls(root, escape)).error,
        (await read(root, escape)).error,
        (await write(root, escape, "overwritten")).error,
        (await edit(root, escape, "secret", "overwritten")).error,
        (await remove(root, escape)).error,
      ]) {
        assert.equal(error, `Error: ${escape} is outside the workstation root`)
      }
    }
    assert.equal(await readFile(outsideFile, "utf8"), "secret")
  })

  it("refuses uploadFiles and downloadFiles as invalid paths", async () => {
    const { root, outsideFile, escapes } = await makeEscapeFixture()

    const uploads = await uploadFiles(
      root,
      escapes.map((escape) => ({
        path: escape,
        content: new Uint8Array([1, 2, 3]),
      }))
    )
    const downloads = await downloadFiles(root, escapes)

    assert.deepEqual(
      uploads.map((response) => response.error),
      escapes.map(() => "invalid_path")
    )
    assert.deepEqual(
      downloads.map((response) => response.error),
      escapes.map(() => "invalid_path")
    )
    assert.deepEqual(
      downloads.map((response) => response.content),
      escapes.map(() => undefined)
    )
    assert.equal(await readFile(outsideFile, "utf8"), "secret")
  })
})
