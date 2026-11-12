/**
 * Integer arithmetic both cores share. Python mirrors each function exactly
 * (`//` for divFloor, `& 0xFFFFFFFF` for the 32-bit multiplies). Keep bitwise
 * operations inside the hash helpers here, and never use `%` on a value that can
 * be negative: JS and Python disagree on its sign.
 */

/** Floor division for b > 0. Exact for every value the sim produces (all < 2^31). */
export function divFloor(a: number, b: number): number {
  return Math.floor(a / b)
}

/** Remainder with the sign of b, for b > 0. */
export function modFloor(a: number, b: number): number {
  return a - b * divFloor(a, b)
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** lowbias32 integer mixer. */
export function mix32(input: number): number {
  let x = input >>> 0
  x ^= x >>> 16
  x = Math.imul(x, 0x7feb352d)
  x ^= x >>> 15
  x = Math.imul(x, 0x846ca68b)
  x ^= x >>> 16
  return x >>> 0
}

/** Counter-based randomness: the same (seed, index, salt) always gives the same uint32. */
export function hash32(seed: number, index: number, salt: number): number {
  return mix32(mix32((seed ^ salt) >>> 0) ^ index)
}

/** An integer in [lo, hi] from a uint32. */
export function pick(hash: number, lo: number, hi: number): number {
  return lo + (hash % (hi - lo + 1))
}

/** FNV-1a 32 over a list of int32 values, each fed as 4 little-endian bytes. */
export function fnv1a(values: readonly number[]): number {
  let hash = 0x811c9dc5
  for (const value of values) {
    const v = value | 0
    for (let shift = 0; shift < 32; shift += 8) {
      hash ^= (v >>> shift) & 0xff
      hash = Math.imul(hash, 0x01000193) >>> 0
    }
  }
  return hash >>> 0
}
