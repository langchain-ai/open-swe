const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const { nextVersion, bumpPackageVersion } = require("../scripts/bump-version.cjs")

function packageFixture(t, version) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-bump-version-"))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const packagePath = path.join(directory, "package.json")
  fs.writeFileSync(
    packagePath,
    `${JSON.stringify({ name: "open-swe-desktop", version, private: true }, null, 2)}\n`
  )
  return packagePath
}

test("increments the requested component and resets the lower ones", () => {
  assert.equal(nextVersion("0.2.3", "patch"), "0.2.4")
  assert.equal(nextVersion("0.2.3", "minor"), "0.3.0")
  assert.equal(nextVersion("0.2.3", "major"), "1.0.0")
  assert.equal(nextVersion("9.9.9", "major"), "10.0.0")
})

test("rejects versions that are not plain three-part semver", () => {
  assert.throws(() => nextVersion("0.2", "patch"), /Invalid semver: 0\.2/)
  assert.throws(() => nextVersion("0.2.3-beta.1", "patch"), /Invalid semver: 0\.2\.3-beta\.1/)
  assert.throws(() => nextVersion("v0.2.3", "patch"), /Invalid semver: v0\.2\.3/)
  assert.throws(() => nextVersion(" 0.2.3", "patch"), /Invalid semver:  0\.2\.3/)
})

test("rejects unknown bump types", () => {
  assert.throws(() => nextVersion("0.2.3", "prerelease"), /Unsupported bump type: prerelease/)
  assert.throws(() => nextVersion("0.2.3", undefined), /Unsupported bump type: undefined/)
})

test("rewrites the package file and returns the new version", (t) => {
  const packagePath = packageFixture(t, "0.2.3")
  assert.equal(bumpPackageVersion(packagePath, "minor"), "0.3.0")
  assert.equal(
    fs.readFileSync(packagePath, "utf8"),
    '{\n  "name": "open-swe-desktop",\n  "version": "0.3.0",\n  "private": true\n}\n'
  )
})

test("leaves the package file untouched when the bump type is unsupported", (t) => {
  const packagePath = packageFixture(t, "0.2.3")
  assert.throws(() => bumpPackageVersion(packagePath, "patchh"), /Unsupported bump type/)
  assert.equal(JSON.parse(fs.readFileSync(packagePath, "utf8")).version, "0.2.3")
})
