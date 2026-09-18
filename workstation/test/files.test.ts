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
let elsewhere: string
let counter = 0

before(async () => {
  base = await mkdtemp(path.join(tmpdir(), "workstation-files-"))
  elsewhere = await mkdtemp(path.join(tmpdir(), "workstation-outside-"))
})

after(async () => {
  await rm(base, { recursive: true, force: true })
  await rm(elsewhere, { recursive: true, force: true })
})

/** Canonical, so a returned real path is directly comparable to `join(root, …)`. */
async function makeRoot(): Promise<string> {
  counter += 1
  const root = path.join(base, `root-${counter}`)
  await mkdir(root, { recursive: true })
  return realpath(root)
}

/** A directory under a different temporary tree than any `makeRoot()`. */
async function makeOutside(): Promise<string> {
  counter += 1
  const dir = path.join(elsewhere, `outside-${counter}`)
  await mkdir(dir, { recursive: true })
  return realpath(dir)
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

  it("reads a real absolute path inside the default directory", async () => {
    const root = await rootWithTenLines()

    const result = await read(root, path.join(root, "lines.txt"), { limit: 1 })

    assert.equal(result.fileData?.content, "line1\n")
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

  it("lists a symlink pointing outside the default directory", async () => {
    const root = await makeRoot()
    const outside = await makeOutside()
    await writeFile(path.join(outside, "notes.txt"), "elsewhere")
    await symlink(outside, path.join(root, "escape"))
    await writeFile(path.join(root, "file.txt"), "ok")

    const result = await ls(root, root)

    assert.equal(result.error, undefined)
    assert.deepEqual(
      result.entries?.map((entry) => entry.path),
      [`${path.join(root, "escape")}/`, path.join(root, "file.txt")]
    )

    const through = await ls(root, "escape")
    assert.deepEqual(
      through.entries?.map((entry) => entry.path),
      [path.join(root, "escape/notes.txt")]
    )
  })

  it("lists a symlink to a sibling under its own path", async () => {
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

describe("paths outside the default directory", () => {
  it("reads, writes, edits, lists, and removes an absolute path elsewhere", async () => {
    const root = await makeRoot()
    const outside = await makeOutside()
    const target = path.join(outside, "notes.txt")

    const written = await write(root, target, "secret\n")
    assert.equal(written.error, undefined)
    assert.equal(written.path, target)

    const edited = await edit(root, target, "secret", "shared")
    assert.equal(edited.error, undefined)
    assert.equal(edited.occurrences, 1)
    assert.equal(await readFile(target, "utf8"), "shared\n")

    const readBack = await read(root, target)
    assert.equal(readBack.error, undefined)
    assert.equal(readBack.fileData?.content, "shared\n")

    assert.deepEqual(
      (await ls(root, outside)).entries?.map((entry) => entry.path),
      [target]
    )

    const removed = await remove(root, target)
    assert.equal(removed.error, undefined)
    assert.deepEqual((await ls(root, outside)).entries, [])
  })

  it("uploads and downloads an absolute path elsewhere", async () => {
    const root = await makeRoot()
    const outside = await makeOutside()
    const target = path.join(outside, "nested/blob.bin")
    const bytes = new Uint8Array([1, 2, 3, 4])

    const uploads = await uploadFiles(root, [{ path: target, content: bytes }])
    const downloads = await downloadFiles(root, [target])

    assert.deepEqual(uploads, [{ path: target }])
    assert.equal(downloads[0]?.error, undefined)
    assert.deepEqual(Array.from(downloads[0]?.content ?? []), Array.from(bytes))
    assert.deepEqual(Array.from(await readFile(target)), Array.from(bytes))
  })

  it("resolves a relative path against the default directory", async () => {
    const root = await makeRoot()

    const written = await write(root, "sub/relative.txt", "here")

    assert.equal(written.path, path.join(root, "sub/relative.txt"))
    assert.equal(
      await readFile(path.join(root, "sub/relative.txt"), "utf8"),
      "here"
    )
    assert.equal(
      (await read(root, "sub/relative.txt")).fileData?.content,
      "here"
    )
  })

  it("reports an unusable path instead of throwing", async () => {
    const root = await makeRoot()

    for (const candidate of ["", "notes\0.txt"]) {
      const expected = `Error: ${candidate} is not a usable filesystem path`
      for (const error of [
        (await ls(root, candidate)).error,
        (await read(root, candidate)).error,
        (await write(root, candidate, "content")).error,
        (await edit(root, candidate, "a", "b")).error,
        (await remove(root, candidate)).error,
      ]) {
        assert.equal(error, expected)
      }

      assert.deepEqual(
        await uploadFiles(root, [
          { path: candidate, content: new Uint8Array([1]) },
        ]),
        [{ path: candidate, error: "invalid_path" }]
      )
      assert.deepEqual(await downloadFiles(root, [candidate]), [
        { path: candidate, error: "invalid_path" },
      ])
    }
  })
})
