# openapi-mcp

Run an MCP server for an OpenAPI JSON specification, or embed the same tools in
another Rust application. The four tools search operations, explain their inputs,
call an API, and inspect component schemas.

## Install from crates.io

The CLI and all three libraries are published at version `0.1.0`.

```sh
cargo install openapi-mcp --version 0.1.0 --locked
openapi-mcp --spec /absolute/path/to/openapi.json
```

## Run locally

```sh
cargo run -p openapi-mcp -- --spec examples/pets.json
```

The server communicates over stdio; diagnostics go to stderr. The API server URL
comes from the specification. Override it with
`--base-url https://api.example.com/v1`, which also supports specifications that
omit `servers`.

The npm distribution is named **openapi-mcp-rs** and launches the `openapi-mcp`
executable. Run the published package with:

```sh
npx --yes openapi-mcp-rs@0.1.0 --spec /absolute/path/to/openapi.json
```

Version `0.1.0` includes native binaries for Linux and macOS on x64/arm64, and
Windows on x64. npm selects the package for your platform automatically.

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

Consuming projects can replace temporary sibling paths with registry dependencies:

```toml
[dependencies]
openapi-mcp-spec = "0.1.0"
openapi-mcp-core = "0.1.0"
openapi-mcp-io = "0.1.0"
```

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
mise install --locked
mise run check
```

Mise supplies the local and CI tools from `mise.toml` and `mise.lock`. Rust is
pinned separately in `rust-toolchain.toml`. The check task runs formatting,
Clippy, version consistency, Rust and npm tests, docs, dependency checks, and
stdio and installed-package smoke tests.

Run individual checks or build the documentation:

```sh
mise run test
mise run package-test
mise run docs
```

The smoke tests exercise a local API through a real MCP process. The package
test builds npm tarballs for the current platform, installs them outside the
checkout, and tests the installed launcher. It does not publish packages.

See [development](docs/src/project/development.md) for the task reference and
prerequisites, [CI](docs/src/project/ci.md) for required checks, and
[distribution](docs/distribution.md) for the packaging and release boundary.
The documentation book is built into `docs/book/`.

## Origin and license

The libraries and launcher were extracted from
[onshape-mcp](https://github.com/altendky/onshape-mcp), starting from commit
`a05fb34`. Onshape-specific authentication, schema annotations, tools, and
resources remain in that project.

Licensed under either [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE).
