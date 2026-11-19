import type {
  JsonObject,
  PullRequest,
  PullRequestSummary,
} from "@openswe/plugin-sdk"

/** A pull request the person pinned, kept in their private storage until they unpin it. */
export interface Pin {
  /** `owner/repo`, lowercase. */
  repository: string
  number: number
  /** The title when it was pinned, shown until GitHub gives the current one. */
  title: string
  pinnedAt: string
}

export type PinKey = `${string}/${string}/${number}`

/** How pins are stored and listed in the inbox's Pinned tab. */
export class Pins {
  static key(repository: string, number: number): PinKey {
    return `${repository.toLowerCase()}/${number}` as PinKey
  }

  /** A stored pin, or `null` for one an older or newer release wrote differently. */
  static read(document: JsonObject): Pin | null {
    const { repository, number, title, pinnedAt } = document
    if (
      typeof repository !== "string" ||
      typeof number !== "number" ||
      typeof pinnedAt !== "string"
    )
      return null
    return {
      repository: repository.toLowerCase(),
      number,
      title: typeof title === "string" ? title : "",
      pinnedAt,
    }
  }

  static document(pin: Pin): JsonObject {
    return {
      repository: pin.repository,
      number: pin.number,
      title: pin.title,
      pinnedAt: pin.pinnedAt,
    }
  }

  /**
   * A pin as the inbox lists pull requests: as GitHub has it now, so it filters
   * and sorts like the other tabs, or as it was pinned when GitHub won't say.
   */
  static summary(pin: Pin, current: PullRequest | null): PullRequestSummary {
    return {
      repository: pin.repository,
      number: pin.number,
      title: current?.title || pin.title || `Pull request #${pin.number}`,
      url:
        current?.url ??
        `https://github.com/${pin.repository}/pull/${pin.number}`,
      draft: current?.draft ?? false,
      author: current?.author ?? null,
      createdAt: current?.createdAt ?? null,
      updatedAt: current?.updatedAt ?? pin.pinnedAt,
    }
  }
}
