# Development

See [README.md](README.md) for architecture and configuration, and
[development](docs/src/project/development.md) for tools and validation tasks.

- Keep `openapi-mcp-spec` and `openapi-mcp-core` sans-IO: use plain-data effects
  and continuations; perform filesystem and network work in the I/O layer.
- Keep vendor-specific authentication, annotations, and validation in host adapters.
- Use `mise install --locked` to install the pinned development tools.
- Run `mise run check` for the development suite, or the relevant individual
  tasks for a focused change. Keep Rust doctests and stdio/npm smoke tests covered.
- Keep npm package versions aligned with the workspace version using
  `python scripts/check-versions.py`.
