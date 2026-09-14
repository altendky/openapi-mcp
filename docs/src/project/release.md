# Releases

All four Rust crates and the six npm packages use one workspace version. The
workflow follows [onshape-mcp's release process](https://github.com/altendky/onshape-mcp/blob/main/docs/src/project/release.md),
with release preparation through a PR and a subsequent development-version PR.
Only stable `X.Y.Z` releases are published. Development versions use
`X.Y.Z-dev.N`; the next version after `0.1.1` is `0.1.2-dev.0`.

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

Automatic publishing and tagging require the repository Actions variable
`RELEASE_ENABLED` to be exactly `true`. Registry setup is complete and this
variable is enabled. The next successful main run at a new stable version can
create its release tag and publish the distributions. Development versions and
versions with an existing matching tag do not create another tag.

The existing release App uses `RELEASE_APP_ID` and `RELEASE_APP_PRIVATE_KEY`.
It creates tags and signed post-release commits/PRs. The automatic GitHub token
creates the GitHub release; it needs no separate long-lived token.

The four crates are published at `0.1.1`, and
[crates.io trusted publishing](https://crates.io/docs/trusted-publishing) is
configured for each crate. The initial publication used a temporary token,
published in dependency order, and verified every registry checksum against the
successful merged-main CI bundle at tag `v0.1.0`.
The [successful `0.1.1` tag run](https://github.com/altendky/openapi-mcp/actions/runs/34793079472)
verified automated OIDC publication for all four crates and
all six npm packages. Registry checksums and GitHub asset digests matched the
original tag-run bundle, and registry smoke tests passed on all five platforms.

[npm trusted publishing](https://docs.npmjs.com/trusted-publishers/) is configured
and verified for the launcher and all five platform packages.
Both registries use the entry workflow filename, `ci.yml`, even though the
publishing jobs are in the called `reflow-release.yml`.
Direct publishing is enabled in npm's publisher settings. There is no GitHub
environment restriction in the publisher configurations or these workflows.

Initial package creation used temporary credentials with access to the intended
crate names and npm scope. Neither `CARGO_REGISTRY_TOKEN` nor `NPM_TOKEN` remains
in the repository's Actions secrets after the completed bootstrap.
The crates.io action obtains a short-lived token; npm uses GitHub OIDC and
provenance. Both publication jobs have `id-token: write`.

The `v0.1.1` GitHub release and all ten registry packages are published. Retain the
original bundle for recovery. A new CI run rebuilds the bundle; it does not reuse
the existing release's assets. When recovering an existing release with a new
run, verify that its entire bundle is byte-identical to the existing release, or
arrange for publishing to consume the original bundle. Cargo and npm skip
existing versions only when their checksums match, and GitHub rejects differing
assets.

### Initial npm bootstrap

The manual `bootstrap-npm.yml` workflow completed `0.1.0` using the original
merged-main bundle from run `34768353819`. It pins the source commit, manifest
checksum, and existing release ID. Before uploading anything it checks the
original CI result, tag, every draft asset digest, and published crate checksums.
Jobs inspecting the draft require GitHub contents write permission because
GitHub restricts draft visibility to callers with push access.

The successful [bootstrap run](https://github.com/altendky/openapi-mcp/actions/runs/34772262263)
published all six npm packages with provenance. Initial registry tests ran before
npm's installation metadata had propagated and could not resolve the native
optional dependencies. After ordinary fresh-cache installs succeeded, rerunning
the failed jobs passed all five platforms and finalized the original release.
No artifacts were rebuilt or replaced.

For read-only verification, its default mode can still be run on `main`:

```sh
gh workflow run bootstrap-npm.yml --ref main
```

The original publishing dispatch used a temporary `NPM_TOKEN` Actions secret
and explicitly requested publication:

```sh
gh workflow run bootstrap-npm.yml --ref main -F publish=true
```

The temporary Actions secret was removed after the successful run.

This publishes the original platform tarballs before the launcher, checks fresh
registry installs and npm exec/npx execution on all five platforms, and only
then finalizes the existing GitHub release. It does not change `RELEASE_ENABLED`
and does not move the tag. npm provenance identifies this later publication
workflow and its actual commit; the original build is identified separately by
the pinned source run, commit, and bundle checksums.

The npm trusted publishers now use the ordinary `ci.yml` workflow. After the
bootstrap, the workspace was advanced to `0.1.1-dev.0` manually; the bootstrap
does not create a development-version PR automatically. Its original Actions
artifact must remain available to rerun bootstrap verification.

## Prepare a release

From a clean `main` checkout with normal Git signing/authentication working:

```sh
mise run release 0.1.2
```

The task pulls main with `--ff-only`, rejects existing release branches/tags and
version downgrades, creates `release/v0.1.2`, synchronizes the workspace/internal
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

npm can accept an upload before the version is visible to readers. Publication
waits up to five minutes per package for matching registry integrity before
continuing to the next package. Registry installation checks also allow five
minutes for installation readiness: a failed install or a missing native
optional package triggers another attempt with a fresh directory and npm cache.
Once the native package resolves, launcher, version, and stdio smoke failures
stop immediately without another installation attempt.

After creating a GitHub draft, publication polls the authenticated release list
for up to two minutes before uploading assets. Draft creation and each npm
upload happen only once per job invocation. These waits report elapsed time;
API errors and mismatched checksums stop immediately. A visibility timeout stops
the job so the service state can be inspected before rerunning failed jobs.

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
