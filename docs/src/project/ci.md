# CI and integrations

GitHub Actions runs CI for pull requests and pushes to `main`. The orchestrator
calls reusable workflows and combines their results in the required check named
exactly `all`. A failed required workflow keeps the aggregate from succeeding.

## Required checks

CI covers:

- The checked-in Mise lockfile and pre-commit configuration.
- Formatting, Clippy, workspace version consistency, and Rust tests.
- Rust 1.89 compatibility, independently of the pinned Rust 1.98 toolchain.
- Nextest results with JUnit reports, plus explicit Rust doctests.
- Native builds and installed npm package tests on Linux x64/ARM64, macOS
  x64/ARM64, and Windows x64. Linux distribution builds use musl targets.
- JavaScript launcher tests and stdio smoke tests against a local API.
- Cargo dependency policy and npm dependency audit.
- Documentation builds, tests, and Markdown link checks.
- Rust and npm coverage, uploaded to Codecov using GitHub Actions OIDC.

Actions are pinned to commit hashes. Workflows use read permissions by default
and disable persistent checkout credentials. Coverage jobs request the OIDC
permission needed for their Codecov uploads.

The coverage service's repository access and the live required-check setting
belong to [GitHub setup #4](https://github.com/altendky/openapi-mcp/issues/4).
The inherited Windows npm test argument problem is tracked separately in
[issue #2](https://github.com/altendky/openapi-mcp/issues/2).

## Dependency and merge automation

`renovate.json5` configures dependency updates, pre-commit hook updates, tool
versions, and pinned GitHub Action digests. The Renovate workflow runs on a
schedule and supports manual dispatch. It uses the existing GitHub App setup:

- `RENOVATE_CLIENT_ID` is a GitHub Actions variable.
- `RENOVATE_APP_PRIVATE_KEY` is a GitHub Actions secret.

The workflow restricts Renovate to this repository and allows Mise lock updates.
Keep the development Rust toolchain and MSRV update policies distinct.

`.mergify.yml` queues eligible non-draft PRs to `main` when they receive the
`enqueue` label. It uses merge commits and approves PRs from the configured
Renovate bot. The release bot's post-release PR approval rule is reserved for
future release automation. Both the Mergify configuration and its repository
access are needed for merge automation to operate.

## Release boundary

CI builds inspectable artifacts and tests local package installation. Publishing
is separate: [release automation #7](https://github.com/altendky/openapi-mcp/issues/7),
[crates.io #5](https://github.com/altendky/openapi-mcp/issues/5), and
[npm #6](https://github.com/altendky/openapi-mcp/issues/6).

Use [onshape-mcp](https://github.com/altendky/onshape-mcp) and
[onshape-export](https://github.com/altendky/onshape-export) as references for
repository policy and workflow conventions. Keep this project's workspace,
platform packages, and standalone smoke tests covered when adapting those
conventions.
