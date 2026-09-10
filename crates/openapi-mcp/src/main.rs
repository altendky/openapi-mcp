//! Standalone `OpenAPI` MCP server over stdio.

use std::{collections::HashMap, path::PathBuf};

use clap::Parser;
use http::{HeaderMap, HeaderName, HeaderValue, header::AUTHORIZATION};
use openapi_mcp_core::{Policy, ToolDefinition, ToolKind, ToolSet};
use openapi_mcp_io::{HttpExecutor, OpenApiMcpServer};
use openapi_mcp_spec::OpenApiSpec;
use rmcp::ServiceExt;
use serde::Deserialize;

type Error = Box<dyn std::error::Error + Send + Sync>;

/// Serve an `OpenAPI` specification as MCP tools over stdio.
#[derive(Parser)]
#[command(version)]
struct Cli {
    /// JSON configuration file. Relative spec paths resolve beside this file.
    #[arg(long)]
    config: Option<PathBuf>,
    /// `OpenAPI` JSON file (overrides config).
    #[arg(long)]
    spec: Option<PathBuf>,
    /// API base URL, overriding the specification's server URL.
    #[arg(long)]
    base_url: Option<String>,
    /// API header as NAME=VALUE. Repeat to configure multiple headers.
    #[arg(long = "header", value_parser = parse_header)]
    headers: Vec<(HeaderName, HeaderValue)>,
    /// Bearer token for API requests (overrides an Authorization header).
    #[arg(long, env = "OPENAPI_MCP_BEARER_TOKEN", hide_env_values = true)]
    bearer_token: Option<String>,
    /// Prefix for the four tool names (default: api).
    #[arg(long)]
    tool_prefix: Option<String>,
    /// Allow API calls to read local files for upload.
    #[arg(long)]
    allow_file_reads: bool,
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    spec: Option<PathBuf>,
    base_url: Option<String>,
    #[serde(default)]
    headers: HashMap<String, String>,
    /// Name of an environment variable; the token itself is not stored here.
    bearer_token_env: Option<String>,
    tool_prefix: Option<String>,
    #[serde(default)]
    allow_file_reads: bool,
}

fn parse_header(value: &str) -> Result<(HeaderName, HeaderValue), String> {
    let (name, value) = value
        .split_once('=')
        .ok_or("header must have the form NAME=VALUE")?;
    let name = HeaderName::try_from(name.trim()).map_err(|_| "invalid header name")?;
    let mut value = HeaderValue::try_from(value).map_err(|_| "invalid header value")?;
    value.set_sensitive(true);
    Ok((name, value))
}

fn tools(prefix: &str) -> Result<ToolSet, String> {
    let names = ["search", "explain", "call", "schema"].map(|suffix| format!("{prefix}_{suffix}"));
    let descriptions = [
        format!(
            "Search API operations. Use {} to inspect an operation before calling it.",
            names[1]
        ),
        format!(
            "Explain an API operation's parameters and schemas. Use {} to inspect component schemas.",
            names[3]
        ),
        format!(
            "Call an API operation. Use {} to inspect required inputs first.",
            names[1]
        ),
        "Look up a component schema, including inherited properties and discriminator options."
            .into(),
    ];
    let kinds = [
        ToolKind::Search,
        ToolKind::Explain,
        ToolKind::Call,
        ToolKind::Schema,
    ];
    ToolSet::new(&std::array::from_fn(|i| ToolDefinition {
        kind: kinds[i],
        name: &names[i],
        description: &descriptions[i],
        input_description: None,
        field_descriptions: &[],
    }))
}

async fn run(cli: Cli) -> Result<(), Error> {
    let mut config: Config = match &cli.config {
        Some(path) => serde_json::from_slice(&tokio::fs::read(path).await?)?,
        None => Config::default(),
    };
    if let Some(path) = &mut config.spec
        && path.is_relative()
        && let Some(directory) = cli.config.as_ref().and_then(|path| path.parent())
    {
        *path = directory.join(&*path);
    }
    let spec_path = cli
        .spec
        .or(config.spec)
        .ok_or("provide --spec or a config containing spec")?;
    let source = tokio::fs::read_to_string(spec_path).await?;
    let base_url = cli.base_url.or(config.base_url);
    let spec = match base_url.as_deref() {
        Some(url) => OpenApiSpec::from_json_with_server_url_fallback(&source, url)?,
        None => OpenApiSpec::from_json(&source)?,
    };
    let mut headers = HeaderMap::new();
    for (name, value) in config.headers {
        let (name, value) = parse_header(&format!("{name}={value}"))?;
        headers.insert(name, value);
    }
    for (name, value) in cli.headers {
        headers.insert(name, value);
    }
    let token =
        match (cli.bearer_token, config.bearer_token_env) {
            (Some(token), _) => Some(token),
            (None, Some(name)) => Some(std::env::var(&name).map_err(|_| {
                format!("token environment variable {name:?} is missing or not UTF-8")
            })?),
            (None, None) => None,
        };
    if let Some(token) = token {
        if token.trim().is_empty() {
            return Err("bearer token must not be blank".into());
        }
        let mut value =
            HeaderValue::try_from(format!("Bearer {token}")).map_err(|_| "invalid bearer token")?;
        value.set_sensitive(true);
        headers.insert(AUTHORIZATION, value);
    }
    let executor = HttpExecutor::new(
        base_url.as_deref().unwrap_or_else(|| spec.server_url()),
        headers.clone(),
    )?;
    let tools = tools(
        cli.tool_prefix
            .as_deref()
            .or(config.tool_prefix.as_deref())
            .unwrap_or("api"),
    )?;
    let server = OpenApiMcpServer::new(
        env!("CARGO_PKG_NAME"),
        env!("CARGO_PKG_VERSION"),
        spec,
        tools,
        Policy::neutral(),
        executor,
    )
    .with_request_headers(&headers)?
    .with_file_reads(cli.allow_file_reads || config.allow_file_reads);
    let service = server.serve(rmcp::transport::stdio()).await?;
    service.waiting().await?;
    Ok(())
}

#[tokio::main]
async fn main() {
    if let Err(error) = run(Cli::parse()).await {
        eprintln!("openapi-mcp: {error}");
        std::process::exit(1);
    }
}
