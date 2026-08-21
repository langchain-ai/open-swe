const fs = require("node:fs")
const path = require("node:path")

const BUMPS = {
  major: ([major]) => [major + 1, 0, 0],
  minor: ([major, minor]) => [major, minor + 1, 0],
  patch: ([major, minor, patch]) => [major, minor, patch + 1],
}

function nextVersion(current, bump) {
  const parsed = /^(\d+)\.(\d+)\.(\d+)$/.exec(current)
  if (!parsed) throw new Error(`Invalid semver: ${current}`)
  const apply = BUMPS[bump]
  if (!apply) throw new Error(`Unsupported bump type: ${bump}`)
  return apply(parsed.slice(1).map(Number)).join(".")
}

function bumpPackageVersion(packagePath, bump) {
  const pkg = JSON.parse(fs.readFileSync(packagePath, "utf8"))
  const next = nextVersion(pkg.version, bump)
  pkg.version = next
  fs.writeFileSync(packagePath, `${JSON.stringify(pkg, null, 2)}\n`)
  return next
}

module.exports = { nextVersion, bumpPackageVersion }

if (require.main === module) {
  const next = bumpPackageVersion(
    path.join(__dirname, "..", "package.json"),
    process.argv[2]
  )
  if (process.env.GITHUB_OUTPUT) {
    fs.appendFileSync(process.env.GITHUB_OUTPUT, `new_version=${next}\n`)
  }
  process.stdout.write(`${next}\n`)
}
