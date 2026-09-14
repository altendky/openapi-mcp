# Distribution

The documentation book's Releases chapter covers version preparation, artifact
validation, publication gates, and recovery. Initial registry setup and
publication are tracked separately in issues #5 and #6.

The Rust workspace has its own version, independent of consuming applications.
All four Rust crates and six npm packages use that version. The npm package
`openapi-mcp-rs` launches the binary `openapi-mcp`.

| Platform | npm binary package |
| --- | --- |
| Linux x64 | `@openapi-mcp-rs/linux-x64` |
| Linux ARM64 | `@openapi-mcp-rs/linux-arm64` |
| macOS x64 | `@openapi-mcp-rs/darwin-x64` |
| macOS ARM64 | `@openapi-mcp-rs/darwin-arm64` |
| Windows x64 | `@openapi-mcp-rs/win32-x64` |

The launcher contains no OpenAPI or authentication logic. Its optional platform
dependencies are injected when packaging, so source development does not require
unpublished platform packages to exist in a registry. `scripts/package-npm.py`
checks the binary version, copies license files, and packs the selected platform
package followed by the launcher. It never publishes.

CI builds and tests each supported native platform, checks the launcher, and
installs the produced tarballs outside the checkout to test actual npm package
resolution. Linux distribution builds use musl targets. Artifacts are retained
for inspection.

All four Rust crates are published on crates.io at `0.1.0`, owned by `altendky`,
with trusted publishing configured for this repository's `ci.yml` workflow.
The initial crate publication used a temporary bootstrap token and the tested
`v0.1.0` artifacts. Library consumers can use registry dependencies, and the CLI
can be installed with `cargo install openapi-mcp --version 0.1.0 --locked`.

All six npm packages are published at `0.1.0` with provenance and have verified
trusted publishers for this repository's `ci.yml` workflow. Direct publishing is
enabled without a GitHub environment restriction. Their registry checksums match
the original tested bundle, and fresh registry installs passed the stdio smoke
test on all five platforms. Run the launcher with
`npx --yes openapi-mcp-rs@0.1.0 --spec /path/to/openapi.json`.

The [GitHub release](https://github.com/altendky/openapi-mcp/releases/tag/v0.1.0)
contains the same complete bundle. The `RELEASE_ENABLED` repository variable is
enabled for automatic tagging and publishing of new stable releases. Ordinary
development and packaging runs do not publish packages. The first automated
OIDC publication remains to be exercised during the next stable release.

Rust publication order is `openapi-mcp-spec`, `openapi-mcp-core`,
`openapi-mcp-io`, then `openapi-mcp`. npm platform packages must be available
before publishing `openapi-mcp-rs` at the same version. After release, consuming
projects replace local Cargo paths with the released versions and regenerate
their lockfiles.
