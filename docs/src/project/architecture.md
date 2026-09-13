# Architecture

The workspace separates plain-data tool logic from networking and process I/O.

| Crate | Responsibility |
| --- | --- |
| `openapi-mcp-spec` | Sans-IO parsing, schema lookup, and request types |
| `openapi-mcp-core` | Sans-IO tools, policies, effects, and continuations |
| `openapi-mcp-io` | Effect runner, HTTP executor, and tools-only MCP server |
| `openapi-mcp` | Configuration and standalone stdio executable |

The parser retains source schema metadata. Hosts supply presentation and
validation callbacks through `Policy`, and can replace `RequestExecutor` for
their own networking or authentication. `Policy::neutral()` uses generic
behavior. Keep vendor-specific authentication, annotations, and validation in
host adapters.

The spec and core crates perform no filesystem or network I/O. Effects describe
work for the I/O layer, and continuations resume processing with its results.
The core's MCP SDK types currently bring runtime dependencies transitively.

This is an operation catalog and request builder, not a complete OpenAPI schema
validator. References are resolved shallowly; component lookup merges one parent
level. The HTTP executor supports JSON and binary multipart request bodies.
