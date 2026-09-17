import { realpath, stat } from "node:fs/promises"

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
  readonly rootDir: string
  readonly execute?: ExecuteConfig
}

/**
 * A deep agents backend over one directory on this machine.
 *
 * The root is resolved and checked here rather than on first use, so a project
 * that has been moved or deleted fails when it is registered instead of
 * halfway through an agent run.
 */
export async function createLocalBackend(
  options: LocalBackendOptions
): Promise<Backend> {
  const rootDir = await realpath(options.rootDir)
  const info = await stat(rootDir)
  if (!info.isDirectory()) {
    throw new Error(`workstation root is not a directory: ${options.rootDir}`)
  }
  const executeConfig = options.execute

  return {
    rootDir,

    ls(target: string): Promise<LsResult> {
      return ls(rootDir, target)
    },

    read(filePath: string, readOptions?: ReadOptions): Promise<ReadResult> {
      return read(rootDir, filePath, readOptions)
    },

    write(filePath: string, content: string): Promise<WriteResult> {
      return write(rootDir, filePath, content)
    },

    edit(
      filePath: string,
      oldString: string,
      newString: string,
      replaceAll?: boolean
    ): Promise<EditResult> {
      return edit(rootDir, filePath, oldString, newString, replaceAll)
    },

    delete(filePath: string): Promise<DeleteResult> {
      return remove(rootDir, filePath)
    },

    grep(pattern: string, grepOptions?: GrepOptions): Promise<GrepResult> {
      return grep(rootDir, pattern, grepOptions)
    },

    glob(pattern: string, searchPath?: string): Promise<GlobResult> {
      return glob(rootDir, pattern, searchPath)
    },

    uploadFiles(files: readonly UploadFile[]): Promise<FileUploadResponse[]> {
      return uploadFiles(rootDir, files)
    },

    downloadFiles(paths: readonly string[]): Promise<FileDownloadResponse[]> {
      return downloadFiles(rootDir, paths)
    },

    execute(
      command: string,
      executeOptions?: ExecuteOptions
    ): Promise<ExecuteResponse> {
      return execute(rootDir, command, executeOptions, executeConfig)
    },

    executeStream(
      command: string,
      executeOptions?: ExecuteOptions
    ): AsyncIterable<ExecuteEvent> {
      return executeStream(rootDir, command, executeOptions, executeConfig)
    },
  }
}
