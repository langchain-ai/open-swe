import type { JsonObject, PullRequestSummary } from "@openswe/plugin-sdk"

/**
 * The last search of an inbox tab, kept in the person's private storage so the
 * tab shows it at once next time while GitHub's search runs again.
 */
export class InboxSnapshot {
  /** The most pull requests a snapshot keeps, newest activity first. */
  static readonly limit = 200

  /** A stored snapshot's pull requests, skipping any an older or newer release wrote differently. */
  static read(document: JsonObject): PullRequestSummary[] {
    const pulls = document.pulls
    if (!Array.isArray(pulls)) return []
    const text = (value: unknown) => (typeof value === "string" ? value : null)
    return pulls.flatMap((value) => {
      if (!value || typeof value !== "object" || Array.isArray(value)) return []
      const { repository, number, title } = value
      if (
        typeof repository !== "string" ||
        typeof number !== "number" ||
        typeof title !== "string"
      )
        return []
      return [
        {
          repository,
          number,
          title,
          url:
            text(value.url) ??
            `https://github.com/${repository}/pull/${number}`,
          draft: value.draft === true,
          author: text(value.author),
          createdAt: text(value.createdAt),
          updatedAt: text(value.updatedAt),
        },
      ]
    })
  }

  static document(pulls: readonly PullRequestSummary[]): JsonObject {
    return {
      pulls: pulls.slice(0, InboxSnapshot.limit).map((pull) => ({
        repository: pull.repository,
        number: pull.number,
        title: pull.title,
        url: pull.url,
        draft: pull.draft,
        author: pull.author,
        createdAt: pull.createdAt,
        updatedAt: pull.updatedAt,
      })),
    }
  }
}
