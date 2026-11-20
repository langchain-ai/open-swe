/** Runs at most `limit` tasks at once, starting the rest in the order they came. */
export class Limiter {
  private running = 0
  private readonly waiting: Array<() => void> = []

  constructor(private readonly limit: number) {}

  run<T>(task: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const start = () => {
        this.running += 1
        Promise.resolve()
          .then(task)
          .then(resolve, reject)
          .finally(() => {
            this.running -= 1
            this.waiting.shift()?.()
          })
      }
      if (this.running < this.limit) start()
      else this.waiting.push(start)
    })
  }
}
