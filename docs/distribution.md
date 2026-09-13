# Distribution

The documentation book's Releases chapter covers version preparation, artifact
validation, publication gates, and recovery. Initial registry setup and
publication remain tracked separately in issues #5 and #6.

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

Publication is a separate step after the local Onshape integration is verified.
The public GitHub repository and its integrations are configured. Publication still
requires confirming ownership of the crate names and npm scope, and configuring
registry credentials or trusted publishers. Release automation requires the
explicit `RELEASE_ENABLED` repository variable, with first publication
to [crates.io in #5](https://github.com/altendky/openapi-mcp/issues/5) and
[npm in #6](https://github.com/altendky/openapi-mcp/issues/6). These development
and ordinary packaging runs do not publish packages.

Rust publication order is `openapi-mcp-spec`, `openapi-mcp-core`,
`openapi-mcp-io`, then `openapi-mcp`. npm platform packages must be available
before publishing `openapi-mcp-rs` at the same version. After release, consuming
projects replace local Cargo paths with the released versions and regenerate
their lockfiles.
