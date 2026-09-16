use std::collections::HashMap;

use http::HeaderMap;
use openapi_mcp_core::{self as core, Effect, Policy, ToolKind, ToolSet};
use openapi_mcp_spec::OpenApiSpec;
use rmcp::{
    ErrorData, RoleServer, ServerHandler,
    model::{
        CallToolRequestParams, CallToolResponse, Implementation, ListToolsResult,
        PaginatedRequestParams, ServerCapabilities, ServerConfig,
    },
    service::RequestContext,
};
use tokio::sync::Mutex;

use crate::api::{self, FileReadPolicy, RequestExecutor};

/// A tools-only MCP server for any parsed `OpenAPI` specification.
///
/// The host supplies metadata, presentation/validation policy, and HTTP execution.
pub struct OpenApiMcpServer<E> {
    info: ServerConfig,
    spec: OpenApiSpec,
    tools: ToolSet,
    policy: Policy,
    executor: Mutex<E>,
    allow_file_reads: bool,
    request_headers: HashMap<String, String>,
}

impl<E: RequestExecutor> OpenApiMcpServer<E> {
    /// Assemble a server. File uploads are disabled until explicitly enabled.
    #[must_use]
    pub fn new(
        name: &str,
        version: &str,
        spec: OpenApiSpec,
        tools: ToolSet,
        policy: Policy,
        executor: E,
    ) -> Self {
        Self {
            info: ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
                .with_server_info(Implementation::new(name, version))
                .with_instructions("Search for an API operation, explain its inputs, then call it. Use schema lookup to inspect referenced component schemas."),
            spec,
            tools,
            policy,
            executor: Mutex::new(executor),
            allow_file_reads: false,
            request_headers: HashMap::new(),
        }
    }

    /// Set whether tool calls may read local files for request uploads.
    #[must_use]
    pub const fn with_file_reads(mut self, allow: bool) -> Self {
        self.allow_file_reads = allow;
        self
    }

    /// Supply configured headers before the engine validates required parameters.
    ///
    /// These headers override tool arguments without exposing values in tool
    /// metadata. Configure the executor with the same headers for HTTP execution.
    ///
    /// # Errors
    /// Returns an error if a header cannot be represented as a textual tool parameter.
    pub fn with_request_headers(
        mut self,
        headers: &HeaderMap,
    ) -> Result<Self, http::header::ToStrError> {
        self.request_headers = headers
            .iter()
            .map(|(name, value)| Ok((name.to_string(), value.to_str()?.to_owned())))
            .collect::<Result<_, _>>()?;
        Ok(self)
    }
}

impl<E: RequestExecutor + 'static> ServerHandler for OpenApiMcpServer<E> {
    fn get_info(&self) -> ServerConfig {
        self.info.clone()
    }

    fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> impl std::future::Future<Output = Result<ListToolsResult, ErrorData>> + Send + '_ {
        std::future::ready(Ok(ListToolsResult::with_all_items(self.tools.list())))
    }

    async fn call_tool(
        &self,
        mut request: CallToolRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<CallToolResponse, ErrorData> {
        let kind = self.tools.resolve(&request.name).ok_or_else(|| {
            ErrorData::invalid_params(format!("unknown tool: {}", request.name), None)
        })?;
        if kind == ToolKind::Call && !self.request_headers.is_empty() {
            let arguments = request.arguments.get_or_insert_with(serde_json::Map::new);
            let params = arguments
                .entry("header_params")
                .or_insert_with(|| serde_json::json!({}));
            // Leave malformed parameters for the engine's usual input error.
            if let Some(params) = params.as_object_mut() {
                for (name, value) in &self.request_headers {
                    params.retain(|key, _| !key.eq_ignore_ascii_case(name));
                    params.insert(name.clone(), serde_json::Value::String(value.clone()));
                }
            }
        }
        let effect = core::dispatch(kind, request.arguments.as_ref(), &self.spec, &self.policy);
        if let Effect::Done(result) = effect {
            return result.map(Into::into);
        }
        let file_reads = if self.allow_file_reads {
            FileReadPolicy::Allow
        } else {
            FileReadPolicy::Deny(
                "Local file uploads are disabled. Enable them with --allow-file-reads.",
            )
        };
        let result = api::run(
            effect,
            &self.policy,
            &mut *self.executor.lock().await,
            file_reads,
        )
        .await;
        result.map(Into::into)
    }
}
