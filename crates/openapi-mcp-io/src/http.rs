use std::time::Duration;

use http::{HeaderMap, header};
use openapi_mcp_spec::request::{ApiRequest, RequestBody};
use rmcp::ErrorData;
use url::Url;

use crate::api::{RequestExecutor, RequestOutcome, Response};

/// Invalid executor configuration.
#[derive(Debug, thiserror::Error)]
pub enum ExecutorBuildError {
    /// The API base URL must identify an HTTP server and optional path prefix.
    #[error("base URL must be an absolute HTTP(S) URL without credentials, query, or fragment")]
    InvalidBaseUrl,
    /// The HTTP client could not be initialized.
    #[error("could not initialize HTTP client: {0}")]
    Client(#[from] reqwest::Error),
}

/// Execute neutral requests against a configured API server.
///
/// Configured headers take precedence over tool-supplied headers, allowing a
/// host to retain control over authentication. JSON and binary multipart bodies
/// use the same request types consumed by custom host executors.
#[derive(Clone)]
pub struct HttpExecutor {
    client: reqwest::Client,
    base_url: String,
    headers: HeaderMap,
}

impl HttpExecutor {
    /// Create an executor with a 30-second timeout and redirects disabled.
    ///
    /// # Errors
    /// Returns an error for an invalid base URL or HTTP client configuration.
    pub fn new(base_url: &str, headers: HeaderMap) -> Result<Self, ExecutorBuildError> {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(30))
            .redirect(reqwest::redirect::Policy::none())
            .build()?;
        Self::with_client(client, base_url, headers)
    }

    /// Use a client with host-configured proxy, TLS, timeout, and redirect policy.
    ///
    /// # Errors
    /// Returns an error for an invalid API base URL.
    pub fn with_client(
        client: reqwest::Client,
        base_url: &str,
        mut headers: HeaderMap,
    ) -> Result<Self, ExecutorBuildError> {
        let url = Url::parse(base_url).map_err(|_| ExecutorBuildError::InvalidBaseUrl)?;
        if !matches!(url.scheme(), "http" | "https")
            || url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(ExecutorBuildError::InvalidBaseUrl);
        }
        for value in headers.values_mut() {
            value.set_sensitive(true);
        }
        Ok(Self {
            client,
            base_url: url.as_str().trim_end_matches('/').to_owned(),
            headers,
        })
    }
}

fn transport_error(error: reqwest::Error) -> ErrorData {
    // URLs may contain sensitive query arguments supplied by an API tool call.
    ErrorData::internal_error(
        format!("HTTP request failed: {}", error.without_url()),
        None,
    )
}

impl RequestExecutor for HttpExecutor {
    async fn execute(&mut self, request: ApiRequest) -> Result<RequestOutcome, ErrorData> {
        if !request.path.starts_with('/') {
            return Err(ErrorData::invalid_params(
                "API request path must start with '/'",
                None,
            ));
        }
        // OpenAPI paths start with '/'. Joining them with Url::join would discard
        // the configured server's path prefix, such as /api/v2.
        let url = format!("{}{}", self.base_url, request.path);
        let mut builder = self
            .client
            .request(request.method, url)
            .query(&request.query_params)
            .headers(request.headers);
        match request.body {
            Some(RequestBody::Json(body)) => {
                builder = builder.json(&body).header(
                    header::CONTENT_TYPE,
                    request
                        .content_type
                        .as_deref()
                        .unwrap_or("application/json"),
                );
            }
            Some(RequestBody::Multipart(body)) => {
                let mut form = reqwest::multipart::Form::new();
                for (name, value) in body.text_fields {
                    form = form.text(name, value);
                }
                for field in body.binary_fields {
                    let mut part = reqwest::multipart::Part::bytes(field.data);
                    if let Some(content_type) = field.content_type {
                        part = part.mime_str(&content_type).map_err(transport_error)?;
                    }
                    form = form.part(field.field_name, part);
                }
                builder = builder.multipart(form);
            }
            None => {}
        }
        let response = builder
            .headers(self.headers.clone())
            .send()
            .await
            .map_err(transport_error)?;
        let status = response.status().as_u16();
        let headers = response
            .headers()
            .iter()
            .map(|(name, value)| {
                (
                    name.to_string(),
                    String::from_utf8_lossy(value.as_bytes()).into_owned(),
                )
            })
            .collect();
        let body = response.bytes().await.map_err(transport_error)?.to_vec();
        Ok(RequestOutcome::Response(Response {
            status,
            headers,
            body,
        }))
    }
}
