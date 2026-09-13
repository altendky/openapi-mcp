# Configuration

Pass an OpenAPI JSON file using `--spec`, or use a configuration file:

```sh
openapi-mcp --config /path/to/config.json
```

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
relative to the working directory. The specification's server URL is used unless
`base_url` or `--base-url` overrides it; an override also supports specifications
that omit `servers`.

CLI settings override corresponding config values. `--header NAME=VALUE` is
repeatable and overrides matching config headers. `--bearer-token` or
`OPENAPI_MCP_BEARER_TOKEN` overrides the configured token environment variable and
the Authorization header. Configured HTTP headers override headers supplied
through tool arguments.

Default tool names are `api_search`, `api_explain`, `api_call`, and `api_schema`.
`--tool-prefix example` changes them to `example_search`, and so on. Local file
uploads require `--allow-file-reads` or `allow_file_reads: true` in the config.
The HTTP executor uses a 30-second timeout and does not follow redirects.

The standalone server supports stdio, OpenAPI JSON, and configured HTTP
headers/bearer tokens. It does not perform OAuth authorization or token refresh.
Embedded hosts can provide their own executor and authentication lifecycle.
