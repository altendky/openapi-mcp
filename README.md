# openapi-mcp

Run an MCP server for an OpenAPI JSON specification, or embed the same tools in
another Rust application. The four tools search operations, explain their inputs,
call an API, and inspect component schemas.

## Run locally

```sh
cargo run -p openapi-mcp -- --spec examples/pets.json
```

The server communicates over stdio; diagnostics go to stderr. The API server URL
comes from the specification. Override it with
`--base-url https://api.example.com/v1`, which also supports specifications that
omit `servers`.

The npm distribution is named **openapi-mcp-rs** and launches the `openapi-mcp`
executable. Once released, its invocation will be:

```sh
npx --yes openapi-mcp-rs --spec /absolute/path/to/openapi.json
```

This repository is being prepared for its first release. Local builds and npm
tarballs can be used before registry publication.

## Configuration

Pass a JSON file with `--config /path/to/config.json`:

```json
{
  "spec": "openapi.json",
  "base_url": "https://api.example.com/v1",
  "headers": { "X-Workspace": "example" },
  "bearer_token_env": "EXAMPLE_API_TOKEN",
  "tool_prefix": "example",
  "allow_file_reads": false
}
```

The spec path in a config file is relative to that file. A `--spec` path is
relative to the working directory. CLI settings override the corresponding
config values. `--header NAME=VALUE` is repeatable and overrides matching config
headers. `--bearer-token` or `OPENAPI_MCP_BEARER_TOKEN` overrides the configured
token environment variable and the Authorization header. Configured HTTP headers
override headers supplied through tool arguments.

Default tool names are `api_search`, `api_explain`, `api_call`, and `api_schema`.
`--tool-prefix example` changes them to `example_search`, and so on. Local file
uploads require `--allow-file-reads` or `allow_file_reads: true` in the config.
The HTTP executor uses a 30-second timeout and does not follow redirects.

This first standalone server supports stdio, OpenAPI JSON, and configured HTTP
headers/bearer tokens. It does not perform OAuth authorization or token refresh.
Embedded hosts can provide their own executor and authentication lifecycle.

## Rust libraries

| Crate | Responsibility |
| --- | --- |
| `openapi-mcp-spec` | Sans-IO parsing, schema lookup, and request types |
| `openapi-mcp-core` | Sans-IO tools, policies, effects, and continuations |
| `openapi-mcp-io` | Effect runner, HTTP executor, and tools-only MCP server |
| `openapi-mcp` | Configuration and standalone stdio executable |

The parser retains source schema metadata. Hosts supply presentation and
validation callbacks through `Policy`, and can replace `RequestExecutor` for
their own networking or authentication. `Policy::neutral()` uses generic
behavior. The core performs no filesystem or network I/O; its MCP SDK types
currently bring runtime dependencies transitively.

This is an operation catalog and request builder, not a complete OpenAPI schema
validator. References are resolved shallowly; component lookup merges one parent
level. The HTTP executor supports JSON and binary multipart request bodies.

## Development

```sh
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cargo build -p openapi-mcp
python scripts/smoke-test.py -- target/debug/openapi-mcp
npm ci --prefix npm/openapi-mcp-rs
npm test --prefix npm/openapi-mcp-rs
```

The smoke test starts a local API and a real MCP process, and checks discovery,
JSON requests, authentication overrides, binary responses, HTTP errors, and
permitted/denied uploads. It also accepts launcher commands such as
`node /absolute/path/to/bin.js` after `--`.

Build npm tarballs for the current platform without publishing:

```sh
python scripts/package-npm.py --binary target/debug/openapi-mcp
python scripts/test-npm-package.py dist
```

The second command installs both generated tarballs into a temporary directory
and runs the smoke test through its `node_modules/openapi-mcp-rs/bin.js`. It may
download the launcher's JavaScript dependencies from npm. Testing outside this
checkout exercises installed platform-package resolution. See [distribution](docs/distribution.md)
for the packaging and release boundary.

## Origin and license

The libraries and launcher were extracted from
[onshape-mcp](https://github.com/altendky/onshape-mcp), starting from commit
`a05fb34`. Onshape-specific authentication, schema annotations, tools, and
resources remain in that project.

Licensed under either [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE).
