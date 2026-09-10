# Development

See [README.md](README.md) for architecture, configuration, and validation.

- Keep `openapi-mcp-spec` and `openapi-mcp-core` sans-IO: use plain-data effects
  and continuations; perform filesystem and network work in the I/O layer.
- Keep vendor-specific authentication, annotations, and validation in host adapters.
- Run formatting, Clippy, Rust tests, and relevant stdio/npm smoke tests for changes.
- Keep npm package versions aligned with the workspace version using
  `python scripts/check-versions.py`.
