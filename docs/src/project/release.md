# Releases

All four Rust crates and the six npm packages use one workspace version. The
workflow follows [onshape-mcp's release process](https://github.com/altendky/onshape-mcp/blob/main/docs/src/project/release.md),
with release preparation through a PR and a subsequent development-version PR.
Only stable `X.Y.Z` releases are published. Development versions use
`X.Y.Z-dev.N`; the next version after `0.1.0` is `0.1.1-dev.0`.

## Validate without publishing

Every PR, main push, and manual CI run validates the release artifacts. It runs
the existing Rust, npm, stdio, MSRV, documentation, security, and coverage checks,
plus:

- `cargo package --workspace --locked`, including building the packaged crates.
  Cargo uses a temporary registry for the unpublished workspace dependencies.
- Native release builds on Linux x64/arm64, macOS x64/arm64, and Windows x64.
  Both Linux binaries use musl and are checked for static linking.
- Local npm tarball installation and stdio smoke tests on all five platforms.
- Native archive packaging with the executable and both license files.
- Assembly of the complete release bundle, checking package identities, exact npm
  optional-dependency versions, identical launcher tarballs, and identical native
  binaries in the npm packages and standalone archives.

The required `all` check includes these checks and bundle assembly. Download the
`release-bundle` artifact from the CI run for inspection. It contains four
`.crate` files, six npm `.tgz` files, four Unix `.tar.gz` archives, one Windows ZIP,
`release.json` with the version/commit/digests, and `SHA256SUMS`.

Local commands, none of which publish:

```sh
mise run release-test
mise run cargo-package
mise run package-test
python scripts/release_artifacts.py pack --binary target/x86_64-unknown-linux-musl/release/openapi-mcp --platform linux-x64
```

For native archives, first build the executable for the requested platform's
target, as CI does. Use the appropriate executable/platform for your machine.
A clean checkout is
required for Cargo packaging; commit the intended changes first. To validate a
downloaded bundle from the same commit:

```sh
python scripts/release_artifacts.py verify /absolute/path/to/release-bundle
```

## Registry setup and first publication

Publishing and automatic tagging are disabled unless the repository Actions
variable `RELEASE_ENABLED` is exactly `true`. Leave it unset while completing
[npm setup (#6)](https://github.com/altendky/openapi-mcp/issues/6). Enabling it
authorizes the next successful stable-version main run to create a tag and
publish the distributions. Do that only when both registries are ready.

The existing release App uses `RELEASE_APP_ID` and `RELEASE_APP_PRIVATE_KEY`.
It creates tags and signed post-release commits/PRs. The automatic GitHub token
creates the GitHub release; it needs no separate long-lived token.

The four crates are published at `0.1.0`, and
[crates.io trusted publishing](https://crates.io/docs/trusted-publishing) is
configured for each crate. The initial publication used a temporary token,
published in dependency order, and verified every registry checksum against the
successful merged-main CI bundle at tag `v0.1.0`.
The publisher configurations have been verified; the first automated OIDC token
exchange remains to be checked when publishing is enabled.

Configure [npm trusted publishing](https://docs.npmjs.com/trusted-publishers/)
for the launcher and all five platform packages after their first publication.
Both registries use the entry workflow filename, `ci.yml`, even though the
publishing jobs are in the called `reflow-release.yml`.
Permit direct publishing in npm's publisher settings. There is no GitHub
environment restriction in these workflows.

Initial package creation may require temporary Actions secrets
`CARGO_REGISTRY_TOKEN` and `NPM_TOKEN`, with access to the intended crate names
and npm scope. Remove the bootstrap secrets after configuring and verifying
the registry publishers.
The crates.io action obtains a short-lived token; npm uses GitHub OIDC and
provenance. Both publication jobs have `id-token: write`.

The `v0.1.0` tag already exists. Its GitHub release remains a draft with the
tested bundle until npm publication and registry smoke checks succeed. Do not
prepare another `0.1.0` PR or move its tag. Complete npm setup and retain the
original bundle for initial publication and recovery. A new CI run rebuilds the
bundle; it does not reuse the draft's assets. Before enabling publication on a
new run, verify that its entire bundle is byte-identical to the draft, or arrange
for publishing to consume the original bundle. Cargo and npm skip existing
versions only when their checksums match, and GitHub rejects differing assets.

### Initial npm bootstrap

The manual `bootstrap-npm.yml` workflow completes `0.1.0` using the original
merged-main bundle from run `34768353819`. It pins the source commit, manifest
checksum, and existing release ID. Before uploading anything it checks the
original CI result, tag, every draft asset digest, and published crate checksums.
Jobs inspecting the draft require GitHub contents write permission because
GitHub restricts draft visibility to callers with push access.

After the workflow is merged, run its default validation-only mode on `main`:

```sh
gh workflow run bootstrap-npm.yml --ref main
```

Once validation passes and the temporary `NPM_TOKEN` Actions secret is configured,
explicitly start publication:

```sh
gh workflow run bootstrap-npm.yml --ref main -F publish=true
```

This publishes the original platform tarballs before the launcher, checks fresh
registry installs and npm exec/npx execution on all five platforms, and only
then finalizes the existing GitHub release. It leaves `RELEASE_ENABLED` unset
and does not move the tag. npm provenance identifies this later publication
workflow and its actual commit; the original build is identified separately by
the pinned source run, commit, and bundle checksums.

After publication, configure npm trusted publishers for the ordinary `ci.yml`
workflow, remove and revoke the temporary token, and prepare the subsequent
development-version PR. This bootstrap does not create that PR automatically.
The original Actions artifact must remain available until bootstrap completes.

## Prepare a release

From a clean `main` checkout with normal Git signing/authentication working:

```sh
mise run release 0.1.1
```

The task pulls main with `--ff-only`, rejects existing release branches/tags and
version downgrades, creates `release/v0.1.1`, synchronizes the workspace/internal
dependency versions, Cargo lockfile, and npm manifests/lockfile, then creates a
signed commit, pushes the branch, and opens a PR. It does not update third-party
dependencies. Any failure stops the command without an automatic retry.

Review the PR and its checks, then approve and merge using the normal repository
protections. Once publishing is enabled:

1. Successful main CI creates the lightweight `vX.Y.Z` tag using the release App.
   It refuses to tag a stale main commit or replace an existing tag.
2. The tag triggers a fresh CI run. The tag must equal the workspace version.
   All checks and artifact validation must pass before publication starts.
   Tag builds install their Rust and Mise tools without restoring their caches.
3. Cargo publishes spec, core, I/O, then CLI. npm publishes all platform packages,
   then the launcher. Publishers verify the bundle against the checked-out commit;
   Cargo also repackages and compares the crates before uploading.
4. Fresh registry npm installations run the stdio smoke test on all five
   platforms. After success, GitHub publishes the release with every artifact
   and its checksums.
5. The release App opens `post-release/vX.Y.Z` to advance main to the next patch's
   `-dev.0` version. GitHub signs the commit and the helper checks the signature.
   Mergify approves these App-authored PRs and queues their `enqueue` label through
   the usual checks and merge protections. Tags themselves are lightweight;
   they are not signed Git tag objects.

The required `all` check describes validation. Publication and the post-release
PR run afterward; inspect the complete tag workflow when checking release
success. A green `all` alone does not mean publication finished.

## Recover an interrupted release

Rerun failed jobs on the original tag run after fixing registry access or a
transient service problem. The workflow serializes publication and never
cancels an active release for a newer run.

Existing crate/npm versions are skipped only if their published checksums match
the validated artifacts. Existing GitHub release assets are likewise checked;
a draft is completed after all assets are present. Different bytes fail and
require investigation. Nothing overwrites published packages, tags, assets, or
post-release branches. If a package upload succeeded but index propagation
timed out, wait for the registry before retrying the failed jobs.

Prefer rerunning failed jobs to rebuilding the entire release: a fresh build
may produce different bytes. Preserve the original run's artifacts while
recovering. If an already-published version differs, do not move its tag or
silently substitute a new build; investigate and prepare a new version as needed.

If main has advanced to another workspace version, the post-release helper skips
the development bump. An existing matching branch or open PR is reused; a stale
checkout, unrelated branch change, or closed unmerged PR stops for inspection.
Fix the underlying condition and retry the post-release job without deleting
someone else's work.
