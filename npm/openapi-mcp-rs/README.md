# openapi-mcp-rs

The npm launcher for the Rust `openapi-mcp` server. It installs the matching
platform binary and passes arguments and stdio directly to it.

```sh
npx --yes openapi-mcp-rs --spec /absolute/path/to/openapi.json
```

Use `--base-url` to override the API server URL, `--config` for a JSON config
file, and `OPENAPI_MCP_BEARER_TOKEN` for bearer authentication. Local uploads
require `--allow-file-reads`.

The packages support Linux x64/ARM64, macOS x64/ARM64, and Windows x64. Linux
release binaries are built for musl. The repository's local packaging command
can also package a native development binary for testing.

When used from the source checkout, the launcher looks for `target/debug/openapi-mcp`
and then `target/release/openapi-mcp`. `OPENAPI_MCP_NPM_COMMAND` overrides binary
selection with a shell-quoted command; shell operators are not supported.

Source, configuration, and Rust API details:
[openapi-mcp](https://github.com/altendky/openapi-mcp).
