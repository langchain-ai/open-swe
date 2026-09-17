import { stat } from "node:fs/promises"
import { homedir } from "node:os"
import path from "node:path"

import { execute, executeStream, type ExecuteConfig } from "./execute.ts"
import {
  downloadFiles,
  edit,
  ls,
  read,
  remove,
  uploadFiles,
  write,
} from "./files.ts"
import { glob, grep } from "./search.ts"
import type {
  Backend,
  DeleteResult,
  EditResult,
  ExecuteEvent,
  ExecuteOptions,
  ExecuteResponse,
  FileDownloadResponse,
  FileUploadResponse,
  GlobResult,
  GrepOptions,
  GrepResult,
  LsResult,
  ReadOptions,
  ReadResult,
  UploadFile,
  WriteResult,
} from "./types.ts"

export interface LocalBackendOptions {
  /**
   * Where a relative path resolves from, and where an `execute` with no cwd
   * runs. Defaults to the user's home directory. Not a boundary: every
   * operation may name any absolute path.
   */
  readonly defaultDir?: string
  readonly execute?: ExecuteConfig
}

/**
 * A deep agents backend over this machine's filesystem.
 *
 * `defaultDir` is checked here rather than on first use, so a project that has
 * been moved or deleted fails when the backend is created instead of halfway
 * through an agent run.
 */
export async function createLocalBackend(
  options: LocalBackendOptions = {}
): Promise<Backend> {
  const defaultDir = path.resolve(options.defaultDir ?? homedir())
  const info = await stat(defaultDir)
  if (!info.isDirectory()) {
    throw new Error(
      `workstation default directory is not a directory: ${defaultDir}`
    )
  }
  const executeConfig = options.execute

  return {
    defaultDir,

    ls(target: string): Promise<LsResult> {
      return ls(defaultDir, target)
    },

    read(filePath: string, readOptions?: ReadOptions): Promise<ReadResult> {
      return read(defaultDir, filePath, readOptions)
    },

    write(filePath: string, content: string): Promise<WriteResult> {
      return write(defaultDir, filePath, content)
    },

    edit(
      filePath: string,
      oldString: string,
      newString: string,
      replaceAll?: boolean
    ): Promise<EditResult> {
      return edit(defaultDir, filePath, oldString, newString, replaceAll)
    },

    delete(filePath: string): Promise<DeleteResult> {
      return remove(defaultDir, filePath)
    },

    grep(pattern: string, grepOptions?: GrepOptions): Promise<GrepResult> {
      return grep(defaultDir, pattern, grepOptions)
    },

    glob(pattern: string, searchPath?: string): Promise<GlobResult> {
      return glob(defaultDir, pattern, searchPath)
    },

    uploadFiles(files: readonly UploadFile[]): Promise<FileUploadResponse[]> {
      return uploadFiles(defaultDir, files)
    },

    downloadFiles(paths: readonly string[]): Promise<FileDownloadResponse[]> {
      return downloadFiles(defaultDir, paths)
    },

    execute(
      command: string,
      executeOptions?: ExecuteOptions
    ): Promise<ExecuteResponse> {
      return execute(defaultDir, command, executeOptions, executeConfig)
    },

    executeStream(
      command: string,
      executeOptions?: ExecuteOptions
    ): AsyncIterable<ExecuteEvent> {
      return executeStream(defaultDir, command, executeOptions, executeConfig)
    },
  }
}
