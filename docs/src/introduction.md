# openapi-mcp

Run an MCP server for an OpenAPI JSON specification, or embed its tools in a Rust
application. The tools search operations, explain inputs, call an API, and inspect
component schemas.

Run the example server from a checkout:

```sh
cargo run -p openapi-mcp -- --spec examples/pets.json
```

The server communicates over stdio and writes diagnostics to stderr. Its API
server URL comes from the specification or a `--base-url` override.

The npm launcher is named `openapi-mcp-rs`; the executable is `openapi-mcp`.
Registry publication is still pending. See [distribution](project/distribution.md)
for local package testing and the release boundary.

The libraries and launcher were extracted from
[onshape-mcp](https://github.com/altendky/onshape-mcp). Vendor-specific
authentication, annotations, tools, and resources remain in host applications.
