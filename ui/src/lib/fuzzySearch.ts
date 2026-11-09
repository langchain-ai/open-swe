export class FuzzySearch {
  private readonly query: string

  constructor(query: string) {
    this.query = query.trim().toLowerCase()
  }

  rank<T>(
    items: ReadonlyArray<T>,
    label: (item: T) => string,
    description: (item: T) => string = () => ""
  ): Array<T> {
    if (!this.query) return [...items]
    return items
      .map((item) => ({
        item,
        score: this.score(label(item), description(item)),
      }))
      .filter(({ score }) => score > 0)
      .sort((a, b) => b.score - a.score)
      .map(({ item }) => item)
  }

  matches(...values: Array<string>): boolean {
    return !this.query || values.some((value) => this.score(value) > 0)
  }

  score(label: string, detail = ""): number {
    const name = label.replace(/^\//, "").toLowerCase()
    const description = detail.toLowerCase()
    if (!this.query) return 200
    if (name.startsWith(this.query)) return 200
    if (name.includes(this.query)) return 150
    if (this.query.length >= 2) {
      const index = description.indexOf(this.query)
      if (index >= 0) {
        return index === 0 || description[index - 1] === " " ? 110 : 90
      }
    }
    if (/\p{N}/u.test(this.query) || this.query.length < 3) return 0
    const score = Math.max(
      this.similarity(name) * 60,
      this.similarity(description) * 30,
      ...name
        .split(/[\\/]/)
        .map((part) => this.similarity(part.replace(/\.[^.]+$/, "")) * 60)
    )
    return score >= 25 ? score : 0
  }

  ranges(text: string): Array<{ start: number; length: number }> {
    if (!this.query) return []
    const exact: Array<{ start: number; length: number }> = []
    const pattern = new RegExp(
      this.query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
      "giu"
    )
    for (const match of text.matchAll(pattern)) {
      exact.push({ start: match.index, length: match[0].length })
    }
    if (exact.length || this.query.length < 3 || /\p{N}/u.test(this.query))
      return exact
    const words = [...text.matchAll(/[\p{L}\p{N}_-]+/gu)]
    const count = this.query.split(/\s+/).length
    return words.flatMap((word, index) => {
      const last = words[index + count - 1]
      if (!last) return []
      const length = last.index + last[0].length - word.index
      return this.similarity(
        text.slice(word.index, word.index + length).toLowerCase()
      ) >= 0.8
        ? [{ start: word.index, length }]
        : []
    })
  }

  private similarity(candidate: string): number {
    const query = this.query
    const positions = new Map<string, Array<number>>()
    for (let i = 0; i < candidate.length; i++) {
      const char = candidate.charAt(i)
      const indices = positions.get(char) ?? []
      indices.push(i)
      positions.set(char, indices)
    }
    if (candidate.length >= 200) {
      for (const [char, indices] of positions) {
        if (indices.length > Math.floor(candidate.length / 100) + 1) {
          positions.delete(char)
        }
      }
    }
    const pending: Array<[number, number, number, number]> = [
      [0, query.length, 0, candidate.length],
    ]
    let matches = 0
    while (pending.length) {
      const range = pending.pop()
      if (!range) break
      const [aStart, aEnd, bStart, bEnd] = range
      let a = aStart
      let b = bStart
      let size = 0
      let previous = new Map<number, number>()
      for (let i = aStart; i < aEnd; i++) {
        const current = new Map<number, number>()
        for (const j of positions.get(query.charAt(i)) ?? []) {
          if (j < bStart) continue
          if (j >= bEnd) break
          const length = (previous.get(j - 1) ?? 0) + 1
          current.set(j, length)
          if (length > size) {
            a = i - length + 1
            b = j - length + 1
            size = length
          }
        }
        previous = current
      }
      while (a > aStart && b > bStart && query[a - 1] === candidate[b - 1]) {
        a--
        b--
        size++
      }
      while (
        a + size < aEnd &&
        b + size < bEnd &&
        query[a + size] === candidate[b + size]
      ) {
        size++
      }
      if (!size) continue
      matches += size
      if (aStart < a && bStart < b) pending.push([aStart, a, bStart, b])
      if (a + size < aEnd && b + size < bEnd) {
        pending.push([a + size, aEnd, b + size, bEnd])
      }
    }
    return (2 * matches) / (query.length + candidate.length)
  }
}
