# Development

Install [Mise](https://mise.jdx.dev/) and
[Rustup](https://rustup.rs/), then run commands from the repository root:

```sh
mise install --locked
mise run check
```

`mise.toml` declares the development tools and tasks; `mise.lock` records tool
versions for the supported platforms. Rust is pinned to 1.98 in
`rust-toolchain.toml`, while the workspace's minimum supported Rust version is
1.89. Keep those two purposes separate when updating tools or dependencies.

Install the MSRV toolchain before running `mise run msrv`:

```sh
rustup toolchain install 1.89 --profile minimal
```

Tool installation, dependency checks, link checks, and npm package tests can
access the network. The pre-commit link hook checks local links offline;
`mise run docs-links` also checks remote URLs.

## Tasks

| Command | Checks |
| --- | --- |
| `mise run check` | Formatting, Clippy, versions, release helper tests, Rust and npm tests, docs, dependency policy, stdio smoke, and installed npm packages |
| `mise run fmt` | Rust formatting |
| `mise run clippy` | All workspace targets and features with warnings denied |
| `mise run versions` | Workspace and npm package version consistency |
| `mise run test` | Workspace Rust tests, with doctests run explicitly |
| `mise run msrv` | Workspace compatibility with Rust 1.89 |
| `mise run npm-test` | JavaScript launcher tests |
| `mise run smoke` | The standalone stdio server against a local test API |
| `mise run package-test` | Build local npm tarballs and test the installed launcher outside the checkout |
| `mise run cargo-package` | Package and build all four Rust crates without publishing (clean checkout) |
| `mise run release-test` | Test version updates, artifacts, and release recovery without publishing |
| `mise run coverage` | Rust and npm test coverage |
| `mise run docs` | Build and test the mdBook documentation |
| `mise run docs-links` | Check Markdown links |
| `mise run pre-commit` | Run the repository's pre-commit hooks |
| `mise run security` | Cargo dependency policy and npm audit, failing on high or critical npm advisories |

Run the individual tasks relevant to a focused change. CI also checks every
supported package platform and the minimum supported Rust version.

Rust crates and npm packages share the workspace version. The version check is
also available directly:

```sh
python scripts/check-versions.py
```

## Smoke and package tests

The stdio smoke test starts a local API and a real MCP process. It checks
discovery, JSON requests, authentication overrides, binary responses, HTTP
errors, and permitted/denied uploads.

To test a specific executable or launcher command:

```sh
python scripts/smoke-test.py -- target/debug/openapi-mcp
python scripts/smoke-test.py -- node /absolute/path/to/bin.js
```

The package test creates the current platform package and launcher tarballs,
installs both into a temporary directory, and runs the smoke test through the
installed launcher. Its install may download JavaScript dependencies from npm.
Testing outside the checkout exercises installed platform-package resolution.
The tarballs remain in `dist/` for inspection; these commands do not publish.

See [Releases](release.md) for complete CI release bundles and the maintainer
release procedure, including the first-publication gate.

## Documentation

Documentation source lives in `docs/src/`, with the chapter list in
`docs/src/SUMMARY.md`. `mise run docs` writes the book to `docs/book/` and tests its
Rust examples. Generated book files are ignored by Git.

`docs/distribution.md` remains the canonical distribution document and is included
in the book. Edit that file when changing packaging guidance.
